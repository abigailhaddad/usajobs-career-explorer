"""Build items from work families, then measure whether the instrument improves.

The families come from pipeline/families.py. They read well and they fixed
the 42% residual, but no external test confirms they are "right" — a work family
deliberately spans grades and conditions, so behavioural coherence is the wrong
bar for them. So this stops arguing about the families and tests the only thing
that matters: do items written from them make a better instrument?

Pass bar, fixed before running. Family items must beat the live 21-item set on
BOTH, on the same 302 occupations:
  * mean profile similarity among the biggest hirers  (lower is better)
  * distinct #1 recommendations across 5,000 takers   (higher is better)
Beating one and losing the other is a fail, and we keep what is live.

    Run as part of stage 5: python run.py --stages 5
"""
import json
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field

from . import llm, s5_questions as s5
from .common import emit

from .config import DATA
PER_FAMILY = 2


class Item(BaseModel):
    text: str = Field(description="A plain description of the activity, in the imperative, "
                                  "the way a job description would put it. No question form, "
                                  "no question mark. One or two sentences.")


class Items(BaseModel):
    items: List[Item]


SYSTEM = (
    "You write items for a career-matching instrument for federal jobs. Someone "
    "rates how interested they are in doing each activity, 0 to 4.\n\n"
    "You are given a FAMILY of occupations that are realistic alternatives to each "
    "other. Write items describing what that family has in common — the kind of work "
    "a person would be choosing, not one job within it.\n\n"
    "This matters because the previous attempt failed the opposite way: items were "
    "written so narrowly ('repair propulsion, rudders and davits') that they were "
    "true of one occupation and scored 0 for 280 others. An item should be true of "
    "most of its family and false of most other families. Aim for something a "
    "reasonable share of federal jobs would score above zero on.\n\n"
    "Write in the imperative, as a description of the activity. Never begin with "
    "'Would you rather', 'Do you like' or any question form.")


def coverage_and_sep(P, hires, names, cfg):
    m = s5._score_instrument(P, hires, names, cfg)
    sd = P.std(1, keepdims=True); sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    rng = np.random.default_rng(0)
    R = rng.integers(0, 5, size=(5000, P.shape[1])).astype(float)
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    top1 = np.argmax(z @ Pz.T / P.shape[1], axis=1)
    return (m["mean_similarity_top30"], len(np.unique(top1)),
            len(m["unresolvable_twins"]), 100 * float((P == 0).mean()))


def run():
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    llm.load_env(cfg["env_file"])
    mc = cfg["model"]
    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}
    lab = np.load(DATA / "llm_labels_12.npy")
    fam_index = pd.read_csv(DATA / "llm_families_index.csv", dtype={"series": str})
    assert list(fam_index.series) == list(facts.series), "family index is out of step with facts"
    hires = facts.hires_entry_perm.to_numpy(float)
    names = facts.series_name.tolist()

    fams = sorted(set(lab), key=lambda c: -(lab == c).sum())
    print(f"{len(fams)} families over {len(facts)} occupations\n")

    def gen(c):
        idx = np.where(lab == c)[0]
        members = "\n".join(
            f"- {facts.iloc[i].series_name}: {(facts.iloc[i].ce_description or '')[:140]}"
            for i in facts.iloc[idx].nlargest(12, "hires_entry_perm").index)
        others = "; ".join(facts.iloc[np.where(lab != c)[0]]
                           .nlargest(10, "hires_entry_perm").series_name)
        return llm.call(
            f"This family of federal occupations:\n{members}\n\n"
            f"Other occupations OUTSIDE the family, which the item should not describe:\n"
            f"{others}\n\n"
            f"Write {PER_FAMILY} items describing the work this family has in common.",
            SYSTEM, Items, mc["generate"], mc["temperature"],
            mc["timeout_seconds"], mc["max_retries"])

    got = llm.map_concurrent(gen, fams, mc["max_concurrent"])
    cands = [it.text for g in got if g for it in g.items]
    print(f"generated {len(cands)} family items")
    for t in cands[:4]:
        print(f"  - {t[:120]}")

    qlist = "\n".join(f"{i}. {t}" for i, t in enumerate(cands))
    smax = cfg["rating"]["scale_max"]

    def rate(r):
        o = llm.call(
            f"Occupation:\n{s5._occ_blurb(r, txt)}\n\n"
            f"Score every statement 0-{smax}. Return one entry per statement id.\n\n{qlist}",
            cfg["rating"]["prompt"], s5.RatingSet, mc["rate"], mc["temperature"],
            mc["timeout_seconds"], mc["max_retries"])
        row = np.full(len(cands), np.nan)
        if o:
            for rt in o.ratings:
                if 0 <= rt.question_id < len(cands):
                    row[rt.question_id] = max(0, min(smax, rt.score))
        return row

    print(f"\nrating {len(facts)} occupations x {len(cands)} items …")
    P = np.vstack(llm.map_concurrent(rate, list(facts.itertuples()), mc["max_concurrent"]))
    miss = float(np.isnan(P).mean())
    if miss > 0.10:
        raise RuntimeError(f"{miss:.0%} of cells unrated — not scoring a failed run")
    P = np.where(np.isnan(P), np.nanmean(P, axis=0), P)

    live = pd.read_parquet(DATA / "generated_profiles_all.parquet")
    lc = sorted([c for c in live.columns if c.startswith("q") and c[1:].isdigit()],
                key=lambda c: int(c[1:]))
    Pl = live.set_index("series").loc[facts.series, lc].to_numpy(float)

    a = coverage_and_sep(Pl, hires, names, cfg)
    b = coverage_and_sep(P, hires, names, cfg)
    res = pd.DataFrame([
        {"instrument": f"live ({Pl.shape[1]} items)", "similarity": round(a[0], 3),
         "distinct_top1": a[1], "unresolvable": a[2], "pct_zero": round(a[3], 1)},
        {"instrument": f"family ({P.shape[1]} items)", "similarity": round(b[0], 3),
         "distinct_top1": b[1], "unresolvable": b[2], "pct_zero": round(b[3], 1)},
    ])
    pd.set_option("display.width", 200)
    print("\n" + res.to_string(index=False))
    better_sep, better_cov = b[0] < a[0], b[1] > a[1]
    print(f"\n  separation better? {better_sep}    coverage better? {better_cov}")
    print(f"  VERDICT: {'PASS — promote the family items' if better_sep and better_cov else 'FAIL — keep the live instrument'}")

    emit(pd.DataFrame({"question_id": range(len(cands)), "text": cands}),
         "family_questions", "question_id")
    prof = pd.DataFrame(P, columns=[f"q{i}" for i in range(len(cands))])
    prof.insert(0, "series_name", names); prof.insert(0, "series", facts.series)
    emit(prof, "family_profiles_all", "series")

