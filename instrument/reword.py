"""Reword items whose second sentence was lifted from the generation axis.

Six of the 25 items ended in a clause restating the axis they were drawn from —
"Work in a rhythm where one urgent call can interrupt the next at any moment",
"Treat each finding as a legal enforcement action", "Repeat the same handling
tasks to a standard". Each reads as machine-written, and nothing in the
objective notices, because the objective only measures whether occupations get
different ratings, never whether a person would want to read the question.

The rewrites below are hand-written. Changing the wording invalidates the
ratings behind it, so this re-rates the whole catalogue against the new text and
writes the result only if the measurements hold up.

    python instrument/reword.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import llm, s5_questions as s5  # noqa: E402
from pipeline.common import emit  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"

# Keyed by the exact current text, so a stale rewrite fails loudly rather than
# silently matching nothing.
REWRITES = {
    "Install, modify and test control and communication systems that keep power generation or spacecraft hardware operating to specification. Enter a licensed engineering track in a cubicle-to-test-lab rhythm.":
        "Install, modify and test the control and communication systems that keep power generation or spacecraft hardware running to specification, splitting your time between a desk and a test lab.",

    "Keep a radio or phone line open for emergencies, send the right unit to the right place, and update crews as the situation changes. Work in a rhythm where one urgent call can interrupt the next at any moment.":
        "Keep a radio or phone line open for emergencies, send the right unit to the right place, and update crews as the situation changes, with one urgent call interrupting the next.",

    "Inspect food, drugs, toys or household products at plants, warehouses or border points, sampling items and documenting violations before unsafe goods reach the public. Treat each finding as a legal enforcement action.":
        "Inspect food, drugs, toys or household products at plants, warehouses or border points, sampling items and documenting violations before unsafe goods reach the public. What you write up can become a legal case.",

    "Track down unpaid federal taxes by meeting taxpayers one case at a time, explaining the debt, arranging collection and taking enforcement steps when needed. Work under tax law and legal procedures.":
        "Track down unpaid federal taxes by meeting taxpayers one case at a time, explaining the debt, arranging collection and taking enforcement steps when the law allows.",

    "Keep a warehouse or commissary stocked by receiving shipments, moving pallets, marking items and putting goods where they can be found quickly. Repeat the same handling tasks to a standard.":
        "Keep a warehouse or commissary stocked by receiving shipments, moving pallets, marking items and putting goods where they can be found quickly, doing the same handling tasks the same way each time.",

    "Process the same vouchers, receipts, and account entries over and over to a set standard.":
        "Process vouchers, receipts and account entries, the same way each time, all day.",

    "Work on a border, checkpoint or inspection line where travellers and shipments must be screened quickly and correctly.":
        "Work on a border, checkpoint or inspection line where travelers and shipments must be screened quickly and correctly.",
}

# The band the current item set measured at, from instrument/promote_mixed.py.
BAND = {"similarity": 0.090, "cov": 205, "ties": 11}


def main():
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    llm.load_env(cfg.get("env_file", ".env"))
    mc = cfg["model"]

    q = pd.read_parquet(DATA / "mixed_questions.parquet")
    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}

    unused = set(REWRITES) - set(q.text)
    if unused:
        raise SystemExit(f"{len(unused)} rewrite(s) matched nothing:\n  "
                         + "\n  ".join(sorted(unused)[:3]))
    texts = [REWRITES.get(t, t) for t in q.text]
    print(f"reworded {sum(a != b for a, b in zip(q.text, texts))} of {len(q)} items")

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

    print(f"rating {len(facts)} occupations x {len(texts)} items …")
    P = np.vstack(llm.map_concurrent(rate, list(facts.itertuples()), mc["max_concurrent"]))
    if np.isnan(P).mean() > 0.10:
        raise RuntimeError("too many unrated cells")
    P = np.where(np.isnan(P), np.nanmean(P, axis=0), P)

    hires = facts.hires_entry_perm.to_numpy(float)
    names = facts.series_name.tolist()
    m = s5._score_instrument(P, hires, names, cfg)
    sd = P.std(1, keepdims=True)
    sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    R = np.random.default_rng(0).integers(0, 5, size=(5000, len(texts))).astype(float)
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    cov = len(np.unique(np.argmax(z @ Pz.T / len(texts), axis=1)))
    sim, ties = m["mean_similarity_top30"], len(m["unresolvable_twins"])

    print(f"\n  similarity {sim:.3f}  distinct#1 {cov}  ties {ties}")
    print(f"  accept if  <= {BAND['similarity']}        >= {BAND['cov']}      <= {BAND['ties']}")
    if not (sim <= BAND["similarity"] and cov >= BAND["cov"] and ties <= BAND["ties"]):
        raise SystemExit("reworded set fell outside the band — not promoting")

    emit(pd.DataFrame({"question_id": range(len(texts)), "text": texts,
                       "origin": list(q.origin)}), "mixed_questions", "question_id")
    prof = pd.DataFrame(P, columns=[f"q{i}" for i in range(len(texts))])
    prof.insert(0, "series_name", names)
    prof.insert(0, "series", facts.series)
    emit(prof, "mixed_profiles_all", "series")
    print("  promoted")


if __name__ == "__main__":
    main()
