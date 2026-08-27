"""Rewrite items whose wording gives away how they were generated.

Six of the 25 end in a clause lifted straight from the generation axis label —
"because the wrong call mostly costs money and inventory accuracy", "where a
wrong call can cost money, mission time or system reliability". The first
sentence of each is fine; the appended clause reads as machine-written and the
metrics are blind to it, because nothing in the objective measures whether a
person would want to read the question.

Rewrites, re-rates the whole catalogue, and only writes the result if the
measurements survive.

    python instrument/fix_phrasing.py
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import llm, s5_questions as s5  # noqa: E402
from pipeline.common import emit  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
TELL = re.compile(r"(because|where)\s+(a|the)\s+wrong\s+(call|decision)", re.I)

# Surgical: cut the borrowed clause, leave every other word untouched. An earlier
# attempt asked a model to rewrite these in plainer language and it produced
# VAGUER language ("operating to specification" -> "working the way they should"),
# which raised correlation between items and doubled the ties, 6 -> 12. Deleting
# the clause cannot introduce vagueness that was not already there.
CUT = re.compile(r"[,.]?\s*(?:because|where|since)\s+(?:a|the|one)\s+wrong\s+"
                 r"(?:call|decision).*$", re.I | re.S)
DANGLE = re.compile(r"\s+and\s+work\s*\.$", re.I)


def trim(t):
    out = CUT.sub(".", t).strip()
    out = DANGLE.sub(".", out)                # "Pass a fitness test and work."
    out = re.sub(r"\s+\.", ".", out)
    return re.sub(r"\.\.+", ".", out)


REWRITE_SYSTEM = (
    "You rewrite items for a career-matching quiz. Someone rates how interested "
    "they are in doing the activity, 0 to 4.\n\n"
    "You are given an item whose closing clause was copied from an internal "
    "category label and reads as machine-written — clauses like 'because the wrong "
    "call mostly costs money and inventory accuracy'. Rewrite the item so it "
    "describes the same work in plain language a seventeen-year-old would read "
    "without wincing.\n\n"
    "Keep the concrete details: the setting, who is there, what is handled. Keep "
    "any real stake, but say it the way a person would ('a mistake can hurt "
    "someone' rather than 'where a wrong call can affect a legal outcome'), or drop "
    "it if it adds nothing. Imperative, one or two sentences, no question form, no "
    "question mark, and do not start with 'Would you rather' or 'Do you like'.")


class Rewrite(BaseModel):
    text: str = Field(description="The rewritten item")


def score(P, hires, names, cfg, R):
    m = s5._score_instrument(P, hires, names, cfg)
    sd = P.std(1, keepdims=True); sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    top1 = np.argmax(z @ Pz.T / P.shape[1], axis=1)
    return m["mean_similarity_top30"], len(np.unique(top1)), len(m["unresolvable_twins"])


def main():
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    llm.load_env(cfg["env_file"])
    mc = cfg["model"]
    q = pd.read_parquet(DATA / "mixed_questions.parquet")
    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}

    bad = [i for i, t in enumerate(q.text) if TELL.search(t)]
    print(f"{len(bad)} of {len(q)} items carry the generated-sounding clause\n")

    texts = list(q.text)
    for i in bad:
        new = trim(q.text[i])
        if TELL.search(new) or len(new) < 60:
            print(f"  !! trim left item {i} unusable, keeping the original")
            continue
        print(f"  before: {q.text[i]}")
        print(f"  after : {new}\n")
        texts[i] = new

    qlist = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
    smax = cfg["rating"]["scale_max"]

    def rate(r):
        o = llm.call(
            f"Occupation:\n{s5._occ_blurb(r, txt)}\n\n"
            f"Score every statement 0-{smax}. Return one entry per statement id.\n\n{qlist}",
            cfg["rating"]["prompt"], s5.RatingSet, mc["rate"], mc["temperature"],
            mc["timeout_seconds"], mc["max_retries"])
        row = np.full(len(texts), np.nan)
        if o:
            for rt in o.ratings:
                if 0 <= rt.question_id < len(texts):
                    row[rt.question_id] = max(0, min(smax, rt.score))
        return row

    print(f"re-rating {len(facts)} occupations x {len(texts)} items …")
    P = np.vstack(llm.map_concurrent(rate, list(facts.itertuples()), mc["max_concurrent"]))
    miss = float(np.isnan(P).mean())
    if miss > 0.10:
        raise RuntimeError(f"{miss:.0%} of cells unrated — refusing to score a failed run")
    P = np.where(np.isnan(P), np.nanmean(P, axis=0), P)

    hires = facts.hires_entry_perm.to_numpy(float)
    names = facts.series_name.tolist()
    old = pd.read_parquet(DATA / "mixed_profiles_all.parquet").set_index("series")
    oc = sorted([c for c in old.columns if c.startswith("q") and c[1:].isdigit()],
                key=lambda c: int(c[1:]))
    Po = old.loc[facts.series, oc].to_numpy(float)
    R = np.random.default_rng(0).integers(0, 5, size=(5000, len(texts))).astype(float)
    a, b = score(Po, hires, names, cfg, R), score(P, hires, names, cfg, R)
    print("\n" + pd.DataFrame([
        {"set": "before rewrite", "similarity": round(a[0], 3), "distinct_top1": a[1], "ties": a[2]},
        {"set": "after rewrite", "similarity": round(b[0], 3), "distinct_top1": b[1], "ties": b[2]},
    ]).to_string(index=False))

    LIVE = (0.029, 154, 20)
    held = b[0] < LIVE[0] and b[1] > LIVE[1] and b[2] < LIVE[2]
    print(f"\n  still beats the live 21-item set on all three: {held}")
    if not held:
        raise SystemExit("rewrite broke the instrument — not writing the result")
    emit(pd.DataFrame({"question_id": range(len(texts)), "text": texts,
                       "origin": list(q.origin)}), "mixed_questions", "question_id")
    prof = pd.DataFrame(P, columns=[f"q{i}" for i in range(len(texts))])
    prof.insert(0, "series_name", names); prof.insert(0, "series", facts.series)
    emit(prof, "mixed_profiles_all", "series")


if __name__ == "__main__":
    main()
