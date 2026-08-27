"""Remove the "and here's what happens if you get it wrong" clauses.

The generator kept appending consequence language borrowed from its internal
axis labels — "because the wrong call mostly costs money and inventory
accuracy", "while every delay affects patient flow". It reads as machine-written
and it is not what the respondent is being asked about: they are rating whether
they want to do the work, not whether they accept the stakes.

Deterministic edits, then a re-rating, because the stored profiles must be built
from the same text the respondent sees. The metrics are reported but not used as
a gate — the wording is a decision, not a measurement.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import llm, s5_questions as s5  # noqa: E402
from pipeline.common import emit  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
PATS = [
    r"[,.]?\s*(?:because|since|where)\s+(?:a|the|one)\s+(?:wrong|missed|bad)\s+\w+.*$",
    r"[,.]?\s*while\s+every\s+delay\s+affects.*$",
    r"[,.]?\s*where\s+the\s+main\s+risk\s+is.*$",
    r"[,.]?\s*because\s+a\s+lapse\s+can.*$",
    r"\s*Make\s+calls\s+where\s+the\s+wrong\s+decision.*$",
    r"\s+for\s+a\s+legal\s+outcome\b",
]
DANGLE = [(r"\s+and\s+work\s*\.$", "."), (r"\s+and\s*\.$", "."), (r",\s*\.$", ".")]


def strip(t):
    out = t
    for p in PATS:
        out = re.sub(p, ".", out, flags=re.I | re.S)
    for a, b in DANGLE:
        out = re.sub(a, b, out, flags=re.I)
    return re.sub(r"\.\.+", ".", re.sub(r"\s+\.", ".", out)).strip()


def main():
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    llm.load_env(cfg["env_file"])
    mc = cfg["model"]
    q = pd.read_parquet(DATA / "mixed_questions.parquet")
    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}

    texts = [strip(t) for t in q.text]
    print(f"stripped consequence clauses from "
          f"{sum(1 for a, b in zip(q.text, texts) if a != b)} of {len(q)} items")

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
    if np.isnan(P).mean() > 0.10:
        raise RuntimeError("too many unrated cells — refusing to write a failed run")
    P = np.where(np.isnan(P), np.nanmean(P, axis=0), P)

    hires = facts.hires_entry_perm.to_numpy(float)
    names = facts.series_name.tolist()
    m = s5._score_instrument(P, hires, names, cfg)
    sd = P.std(1, keepdims=True); sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    R = np.random.default_rng(0).integers(0, 5, size=(5000, len(texts))).astype(float)
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    cov = len(np.unique(np.argmax(z @ Pz.T / len(texts), axis=1)))
    print(f"\n  similarity {m['mean_similarity_top30']:.3f}  distinct#1 {cov}  "
          f"ties {len(m['unresolvable_twins'])}")
    print("  (mixed set measured over 3 draws: 0.069-0.083 | 210-211 | 5-9;")
    print("   live 21-item set: 0.022-0.029 | 142-156 | 20-22)")

    emit(pd.DataFrame({"question_id": range(len(texts)), "text": texts,
                       "origin": list(q.origin)}), "mixed_questions", "question_id")
    prof = pd.DataFrame(P, columns=[f"q{i}" for i in range(len(texts))])
    prof.insert(0, "series_name", names); prof.insert(0, "series", facts.series)
    emit(prof, "mixed_profiles_all", "series")


if __name__ == "__main__":
    main()
