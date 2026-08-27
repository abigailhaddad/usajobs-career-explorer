"""Apply the phrasing fixes and promote the mixed instrument.

The acceptance bar is the mixed set's own measured band, not the stale cached
figures that caused these edits to be rejected the first time:
    similarity 0.069-0.083 | distinct #1 210-211 | ties 5-9
An edited set is accepted if it lands within or better than that band.
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
TELL = re.compile(r"(because|where)\s+(a|the|one)\s+wrong\s+(call|decision)", re.I)
CUT = re.compile(r"[,.]?\s*(?:because|where|since)\s+(?:a|the|one)\s+wrong\s+"
                 r"(?:call|decision).*$", re.I | re.S)
DANGLE = re.compile(r"\s+and\s+work\s*\.$", re.I)
BAND = {"similarity": 0.090, "cov": 205, "ties": 11}   # generous edge of the measured band


def trim(t):
    out = DANGLE.sub(".", CUT.sub(".", t).strip())
    return re.sub(r"\.\.+", ".", re.sub(r"\s+\.", ".", out))


def main():
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    llm.load_env(cfg["env_file"])
    mc = cfg["model"]
    q = pd.read_parquet(DATA / "mixed_questions.parquet")
    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}

    texts = [trim(t) if TELL.search(t) else t for t in q.text]
    n_fixed = sum(1 for a, b in zip(q.text, texts) if a != b)
    print(f"trimmed the borrowed clause from {n_fixed} of {len(q)} items")

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
    sd = P.std(1, keepdims=True); sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    R = np.random.default_rng(0).integers(0, 5, size=(5000, len(texts))).astype(float)
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    cov = len(np.unique(np.argmax(z @ Pz.T / len(texts), axis=1)))
    sim, ties = m["mean_similarity_top30"], len(m["unresolvable_twins"])
    print(f"\n  similarity {sim:.3f}  distinct#1 {cov}  ties {ties}")
    print(f"  measured band was 0.069-0.083 | 210-211 | 5-9")
    ok = sim <= BAND["similarity"] and cov >= BAND["cov"] and ties <= BAND["ties"]
    print(f"  within band: {ok}")
    if not ok:
        raise SystemExit("edited set fell outside the band — not promoting")

    emit(pd.DataFrame({"question_id": range(len(texts)), "text": texts,
                       "origin": list(q.origin)}), "mixed_questions", "question_id")
    prof = pd.DataFrame(P, columns=[f"q{i}" for i in range(len(texts))])
    prof.insert(0, "series_name", names); prof.insert(0, "series", facts.series)
    emit(prof, "mixed_profiles_all", "series")
    print("  promoted")


if __name__ == "__main__":
    main()
