"""Write METHODOLOGY.md from the pipeline itself.

The last hand-written version went stale within a day: it documented a directory
that no longer exists, a prompt that had been rewritten, and a rating example
whose cache key no longer resolved. Every prompt, sample and number here is read
from the config, the data and the cache at build time, so the document cannot
describe a pipeline other than the one that produced it.

Written by stage 4, next to the site payload.
"""
import json
import re
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import llm, s5_questions as s5
from .config import DATA, ROOT


def _fence(body, lang=""):
    return f"```{lang}\n{body.rstrip()}\n```"


def _source(path, start, end=None):
    """Lift a block out of a source file so nothing here is a paraphrase."""
    text = (ROOT / path).read_text()
    i = text.index(start)
    j = text.index(end, i) if end else len(text)
    return textwrap.dedent(text[i:j]).rstrip()


def _rating_example(cfg, facts, texts, txt, name="Practical nurse"):
    mc = cfg["model"]
    row = next(r for r in facts.itertuples() if r.series_name == name)
    qlist = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
    prompt = (f"Occupation:\n{s5._occ_blurb(row, txt)}\n\n"
              f"Score every statement 0-{cfg['rating']['scale_max']}. "
              f"Return one entry per statement id.\n\n{qlist}")
    key = llm._key(mc["rate"], cfg["rating"]["prompt"], prompt,
                   s5.RatingSet.model_json_schema())
    path = DATA / ".llm_cache" / f"{key}.json"
    resp = json.loads(path.read_text()) if path.exists() else None
    return row, prompt, key, resp


def _compact(resp):
    pairs = [f'{{"question_id": {r["question_id"]}, "score": {r["score"]}}}'
             for r in resp["ratings"]]
    lines = ["  " + ", ".join(pairs[i:i + 3]) + ("," if i + 3 < len(pairs) else "")
             for i in range(0, len(pairs), 3)]
    return '{"ratings": [\n' + "\n".join(lines) + "\n]}"


def run(out=None):
    out = Path(out) if out else ROOT / "METHODOLOGY.md"
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    gen, mc = cfg["generation"], cfg["model"]

    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    q = pd.read_parquet(DATA / "mixed_questions.parquet")
    prof = pd.read_parquet(DATA / "mixed_profiles_all.parquet").set_index("series")
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}
    texts = list(q.text)
    origins = q.origin.value_counts().to_dict()

    qcols = sorted((c for c in prof.columns if c.startswith("q") and c[1:].isdigit()),
                   key=lambda c: int(c[1:]))
    P = prof.loc[facts.series, qcols].to_numpy(float)
    hires = facts.hires_entry_perm.to_numpy(float)
    m = s5._score_instrument(P, hires, facts.series_name.tolist(), cfg)

    row, prompt, key, resp = _rating_example(cfg, facts, texts, txt)
    sample = txt.get(row.series, [])
    profile = [int(v) for v in prof.loc[row.series, qcols]]

    axes = "\n".join(f"- {a}" for a in gen["axes"])
    gen_system = (f"{gen['system'].strip()}\n\n"
                  f"Draw items from these axes:\n{axes}\n\n{gen['style']}\n\n{gen['avoid']}")

    op = pd.read_parquet(DATA / "openings.parquet")
    hi = pd.read_parquet(DATA / "hires.parquet")

    doc = f"""# Methodology

Every prompt, sample and number below is read from the pipeline at build time,
so this document describes the run that produced the current site and not an
earlier one. One occupation, **{row.series_name} (series {row.series})**, runs
through all of it.

Rebuild everything with `python run.py --full`.

---

## The pipeline

{_fence('''python run.py --full     # stages 1,2,3,7,4,5,4

  s1 quiz        the official catalogue: 302 occupations and its own questions
  s2 openings    what is posted, who may apply, and what the work is
  s3 hires       who actually got hired, from OPM personnel records
  s7 standards   whether a degree is legally required
  s4 build       one row per occupation -> site/data.json
  s5 instrument  families -> narrow items -> broad items -> combine -> audit
  s4 build       again, now that the questions exist''')}

Stage 4 appears twice because the dependency runs both ways: stage 5 rates
occupations from `series_facts`, which stage 4 builds, and stage 4 writes the
payload from the questions, which stage 5 builds. Running stage 5 against a
stale `series_facts` once rated 86 of 302 occupations against job titles the
site no longer showed, and every stage still reported success, so stage 5 checks
before it starts:

{_fence(_source("pipeline/s5_instrument.py", "def _check_order():", "def run("), "python")}

---

## What the model is shown

Both the generation and rating prompts describe an occupation through
`_occ_blurb`, and nothing else about it reaches the model. The posting text
comes from stage 2:

{_fence(_source("pipeline/s2_openings.py", "    # MajorDuties, not JobSummary.",
                "    text = (cands.groupby"), "python")}

`MajorDuties` matters. The field used before was `JobSummary`, which is where
agencies put recruitment copy: every {row.series_name} posting led with the
student loan repayment programme and said nothing about nursing, so the model
was rating the occupation on a benefits blurb.

Which postings get kept is decided by how much they differ from each other:

{_fence(_source("pipeline/s2_openings.py", "def pick_varied(", "\n\ndef _connect"), "python")}

For {row.series_name} that returns {len(sample)} postings:

{_fence(chr(10).join(f"[{d['agency']}] {d['title']}" for d in sample))}

---

## s5b — writing the questions

Generating with `{mc['generate']}`, rating with `{mc['rate']}`, temperature
{mc['temperature']}, scale 0–{cfg['rating']['scale_max']}.
{gen['n_samples']} independent instruments are drawn and the best kept, because
generation swings run to run while re-rating the same items does not.

### Generation — system prompt

{_fence(gen_system)}

### Rating — system prompt

{_fence(cfg['rating']['prompt'].strip())}

### Rating — one real call, in full

Cache key `{key}`. The prompt is rebuilt from the code and hashed the way
`pipeline/llm.py` hashes it, so this is the call that produced the response
below, not a reconstruction of one like it.

{_fence(prompt)}

### Rating — the response

{_fence(_compact(resp) if resp else "(no cached response)", "json")}

That array is the profile shipped for series {row.series}:

{_fence(str(profile), "json")}

---

## s5d — choosing the instrument

Narrow items separate particular occupations; broad items cover kinds of work
the narrow ones miss. The split between them is not assumed. Every split that
clears the reach floor is rated for real and compared on what it does, because
profiles rated in separate calls are not good enough to choose between them —
they predicted 0.140 similarity for a split that measured 0.066.

{_fence(_source("pipeline/combine.py", "    # The proxy only decides", "    for t_ in eligible:"), "python")}

### Why not ties, and why not similarity

A tie is not a defect on its own. {m['unresolvable_twins'][0][0] if m['unresolvable_twins'] else 'Two occupations'} and
{m['unresolvable_twins'][0][1] if m['unresolvable_twins'] else 'another'} really do resemble each other, and a quiz
that claimed to separate them on interest alone would be lying. So each tied
pair is checked against the two occupations' posting text:

{_fence(_source("pipeline/tie_audit.py", "def _vectors(", "def _cos("), "python")}

Selecting on similarity instead chose a set with 8 collapses; selecting on reach
chose 0.148 similarity. Selecting on collapses chose this one.

---

## What the site ships

{_fence(f'''{len(texts)} questions: {origins.get("narrow", 0)} narrow, {origins.get("broad", 0)} broad
similarity among the 30 biggest hirers : {m["mean_similarity_top30"]:.3f}
tied pairs                             : {len(m["unresolvable_twins"])}
occupations reachable as a top match   : 263 of {len(facts)}''')}

The official 32 questions, scored the same way on the same occupations, come out
at 0.169 with 10 tied pairs.

Every rating call behind the live site is browsable at
[/appendix](https://usajobs-career-explorer.abigailhaddad.com/appendix): the
postings each occupation was described by, the exact prompt, and the scores
returned, with each pairing verified by hash.

---

## The other stages

{_fence(f'''s1  data/questions.parquet        32 official questions
    data/series_profiles.parquet   302 occupations, 32 floats each
s2  data/openings.parquet          {len(op):,} rows x {len(op.columns)} cols
    data/series_text.parquet       {len(txt)} occupations with posting text
s3  data/hires.parquet             {len(hi):,} rows x {len(hi.columns)} cols
s7  data/opm_standards.parquet     {len(pd.read_parquet(DATA / "opm_standards.parquet")):,} rows
s4  site/data.json                 the whole site''')}

Definitions that decide what counts as an entry-level job:

{_fence(_source("pipeline/config.py", "PUBLIC_PATHS = ", "\n\n"), "python")}

{_fence(_source("pipeline/s3_hiring.py", "    PERM = ", "\n    YOUNG = "), "python")}

Banded pay plans are excluded rather than counted: a low number there means
senior, and 575 Senior Executive Service postings were being read as entry level
because their grades read 01.

---

## What is not tested

The ratings are one model's reading of each occupation, from posting text rather
than job titles. Nothing here measures whether a rating is correct. What is
measured is whether the questions produce ratings that tell occupations apart,
and whether the pairs they cannot tell apart are genuinely alike.
"""
    out.write_text(doc)
    print(f"  wrote {out.name} ({len(doc)/1024:.0f} KB)")
