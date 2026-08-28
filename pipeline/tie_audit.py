"""Which ties are real, and which are the instrument collapsing different jobs.

A tie is not a defect on its own. Nursing assistant and practical nurse do the
same kind of work, and an interest quiz claiming to tell them apart is lying.
The defect is the other case: two occupations scored alike whose postings
describe different work.

So each tied pair is checked against the duties text stage 2 sampled for both,
compared by IDF-weighted cosine. Plain word overlap does not work here: federal
postings share so much vocabulary that "information technology management" and
"supply clerical" came out as the single most similar pair in the catalogue.
Weighting by how rare a word is across occupations puts nursing assistant next
to practical nurse and custodial working next to food service, which is what
alike is supposed to mean.

The cutoff is a percentile of this run's own pairs rather than a fixed number,
because the absolute values depend on how much text each sample happened to
carry.

    Runs at the end of stage 5, and importable for a one-off check.
"""
import itertools
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import s5_questions as s5

from .config import DATA

# A tied pair is honest if its postings are among the most alike in the
# catalogue. Ties above this percentile of all top-30 pairs are expected.
ALIKE_PERCENTILE = 90


def _tokens(s):
    return re.findall(r"[a-z]{4,}", (s or "").lower())


def _vectors(docs):
    """TF-IDF, normalised, one vector per occupation."""
    n = len(docs)
    df = Counter()
    for t in docs.values():
        df.update(set(t))
    idf = {w: math.log(n / c) for w, c in df.items()}
    out = {}
    for s, t in docs.items():
        v = {w: (1 + math.log(c)) * idf.get(w, 0.0) for w, c in Counter(t).items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out[s] = {w: x / norm for w, x in v.items()}
    return out


def _cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def collapses(P, facts, cfg, txt):
    """How many tied pairs are the instrument losing genuinely different work.

    Separation and reach can both be bought at the cost of this, so selection
    reads it directly rather than trusting either as a proxy for it.
    """
    docs = {r.series: _tokens(" ".join(d.get("duties") or d.get("summary") or ""
                                      for d in txt.get(r.series, [])))
            for r in facts.itertuples()}
    docs = {s: tk for s, tk in docs.items() if tk}
    V = _vectors(docs)
    hires = facts.hires_entry_perm.to_numpy(float)
    top = [i for i in np.argsort(-hires)[:30] if facts.series.iloc[i] in V]
    pairs = {}
    for i, j in itertools.combinations(top, 2):
        a, b = facts.series.iloc[i], facts.series.iloc[j]
        pairs[frozenset((a, b))] = _cos(V[a], V[b])
    if not pairs:
        return 0
    cutoff = float(np.percentile(list(pairs.values()), ALIKE_PERCENTILE))
    m = s5._score_instrument(P, hires, facts.series_name.tolist(), cfg)
    by_name = dict(zip(facts.series_name, facts.series))
    n = 0
    for a, b, _ in m["unresolvable_twins"]:
        ts = pairs.get(frozenset((by_name.get(a), by_name.get(b))))
        if ts is not None and ts < cutoff:
            n += 1
    return n


def run(profiles=None):
    profiles = profiles or DATA / "mixed_profiles_all.parquet"
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    prof = pd.read_parquet(profiles).set_index("series")
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}

    qcols = sorted((c for c in prof.columns if c.startswith("q") and c[1:].isdigit()),
                   key=lambda c: int(c[1:]))
    facts = facts[facts.series.isin(prof.index)].reset_index(drop=True)
    P = prof.loc[facts.series, qcols].to_numpy(float)
    hires = facts.hires_entry_perm.to_numpy(float)

    docs = {r.series: _tokens(" ".join(d.get("duties") or d.get("summary") or ""
                                       for d in txt.get(r.series, [])))
            for r in facts.itertuples()}
    docs = {s: t for s, t in docs.items() if t}
    V = _vectors(docs)

    top = [i for i in np.argsort(-hires)[:30] if facts.series.iloc[i] in V]
    pairs = {}
    for i, j in itertools.combinations(top, 2):
        a, b = facts.series.iloc[i], facts.series.iloc[j]
        pairs[frozenset((a, b))] = _cos(V[a], V[b])
    cutoff = float(np.percentile(list(pairs.values()), ALIKE_PERCENTILE)) if pairs else 0.0

    m = s5._score_instrument(P, hires, facts.series_name.tolist(), cfg)
    by_name = dict(zip(facts.series_name, facts.series))

    rows = []
    for a, b, sim in m["unresolvable_twins"]:
        sa, sb = by_name.get(a), by_name.get(b)
        ts = pairs.get(frozenset((sa, sb)))
        verdict = "unknown" if ts is None else ("alike" if ts >= cutoff else "collapsed")
        rows.append({"a": a, "b": b, "similarity": sim,
                     "text": None if ts is None else round(ts, 3), "verdict": verdict})

    print(f"{Path(profiles).name}: similarity {m['mean_similarity_top30']:.3f}, "
          f"{len(rows)} tied pairs among the 30 biggest hirers")
    print(f"postings count as alike above {cutoff:.3f} "
          f"(p{ALIKE_PERCENTILE} of {len(pairs)} pairs)\n")
    if not rows:
        print("  no ties")
        return rows
    order = {"collapsed": 0, "unknown": 1, "alike": 2}
    for r in sorted(rows, key=lambda r: (order[r["verdict"]], -r["similarity"])):
        mark = {"collapsed": "!!", "unknown": " ?", "alike": "  "}[r["verdict"]]
        ts = "  n/a" if r["text"] is None else f"{r['text']:.3f}"
        print(f"  {mark} sim {r['similarity']:.3f}  text {ts}  {r['a']} | {r['b']}")
    bad = sum(1 for r in rows if r["verdict"] == "collapsed")
    print(f"\n  {len(rows) - bad} defensible, {bad} collapsing occupations whose postings differ")
    return rows

