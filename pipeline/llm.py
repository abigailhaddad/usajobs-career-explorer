"""Structured LLM calls: litellm + a Pydantic response_format, with retry.

Same shape as the generic-comment-analyzer: litellm.completion with
response_format=<PydanticModel>, temperature 0, drop_params on so reasoning
models that reject temperature still work.
"""
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Type, TypeVar

import litellm
from pydantic import BaseModel

litellm.drop_params = True   # e.g. GPT-5 reasoning models reject temperature
T = TypeVar("T", bound=BaseModel)

# Responses are cached on disk, keyed by (model, system, prompt, schema).
# Two reasons this is not just a cost optimisation: these models are not actually
# deterministic at temperature 0, and this pipeline is diffed run to run. Without
# a cache, every re-run reports model noise as though the data had changed.
CACHE = Path(__file__).resolve().parent.parent / "data" / ".llm_cache"


def _key(model, system, prompt, schema) -> str:
    blob = json.dumps([model, system, prompt, schema], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def load_env(env_file: str = ".env"):
    """Read KEY=value from an .env we do not own. Never copies it into this repo.

    A missing file is fine when OPENAI_API_KEY is already set in the environment,
    so the key can come from either place.
    """
    p = Path(env_file).expanduser()
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            f"OPENAI_API_KEY is not set and was not found in {p}. Export it, or "
            f"point env_file in pipeline/questions_config.yaml at a file holding it.")


def call(prompt: str, system: str, output_model: Type[T], model: str,
         temperature: float = 0.0, timeout: int = 90,
         max_retries: int = 3, verbose: bool = False,
         use_cache: bool = True) -> Optional[T]:
    """One structured call. Returns a validated model, or None if it never parsed.

    Cached on disk: an unchanged (model, system, prompt, schema) returns the
    previous response without an API call. Delete data/.llm_cache to force fresh.
    """
    schema = output_model.model_json_schema()
    path = CACHE / f"{_key(model, system, prompt, schema)}.json"
    if use_cache and path.exists():
        return output_model(**json.loads(path.read_text()))

    last = None
    for attempt in range(max_retries):
        try:
            r = litellm.completion(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                response_format=output_model,
                temperature=temperature,
                timeout=timeout,
            )
            data = json.loads(r.choices[0].message.content)
            out = output_model(**data)
            if use_cache:
                CACHE.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
            return out
        except Exception as e:                       # noqa: BLE001 - retried below
            last = e
            if verbose:
                print(f"    retry {attempt+1}/{max_retries}: {str(e)[:90]}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    print(f"    !! gave up after {max_retries}: {str(last)[:140]}")
    return None


def map_concurrent(fn, items, max_concurrent=6):
    """Run fn over items with a small thread pool, preserving order."""
    with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
        return list(ex.map(fn, items))
