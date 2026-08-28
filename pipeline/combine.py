"""Combine the narrow and broad items into the set the site ships.

The two halves are generated separately and rated separately, so their profiles
cannot simply be pasted side by side: each rating call only ever saw its own
list of statements. Selection uses the separate ratings as a proxy, and then the
chosen set is re-rated in one pass, the way the site's profiles are actually
produced.

Why both halves exist. Narrow items separate the big hirers but are written to
split particular pairs, so whole kinds of work end up with nothing a person can
react to, and the quiz can only ever recommend what its items reach. Broad items
cost separation and buy back reach.

Selection keeps every narrow item that discriminates, then adds broad items in
the order that most improves how many occupations can come out on top.

    Run as part of stage 5: python run.py --stages 5
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import llm, s5_questions as s5
from .common import emit
from .tie_audit import collapses, run as audit

from .config import DATA

# Reach the boilerplate-era instrument managed was 232, so the floor is set
# above it: a rebuild should not be able to ship a narrower quiz than the one
# it replaces.
REACH_FLOOR = 250


def coverage(P, n=5000, seed=0):
    """How many occupations can come out as somebody's top match."""
    sd = P.std(1, keepdims=True)
    sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    R = np.random.default_rng(seed).integers(0, 5, size=(n, P.shape[1])).astype(float)
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    return len(np.unique(np.argmax(z @ Pz.T / P.shape[1], axis=1)))


def rate_all(texts, facts, txt, cfg, mc):
    """One call per occupation, showing every statement at once.

    This is how the site's profiles are produced, so a candidate set has to be
    rated this way before its numbers mean anything.
    """
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

    P = np.vstack(llm.map_concurrent(rate, list(facts.itertuples()), mc["max_concurrent"]))
    if np.isnan(P).mean() > 0.10:
        raise RuntimeError("too many unrated cells")
    return np.where(np.isnan(P), np.nanmean(P, axis=0), P)


def _profiles(path, series):
    df = pd.read_parquet(path).set_index("series")
    cols = sorted((c for c in df.columns if c.startswith("q") and c[1:].isdigit()),
                  key=lambda c: int(c[1:]))
    return df.loc[series, cols].to_numpy(float)


def run(n_final=25, n_narrow=None):
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    llm.load_env(cfg.get("env_file", ".env"))
    mc = cfg["model"]

    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}
    narrow = pd.read_parquet(DATA / "generated_questions.parquet")
    broad = pd.read_parquet(DATA / "family_questions.parquet")

    series = facts.series
    Pn = _profiles(DATA / "generated_profiles_all.parquet", series)
    Pb = _profiles(DATA / "family_profiles_all.parquet", series)
    hires = facts.hires_entry_perm.to_numpy(float)
    names = facts.series_name.tolist()
    print(f"{len(narrow)} narrow + {len(broad)} broad items, {len(facts)} occupations")

    # Proxy selection, on the separately-rated profiles. Narrow items are ranked
    # by how much they discriminate where the hiring is; broad items are added
    # greedily for reach. The split between them is swept rather than assumed:
    # every narrow item clears the variance floor, so a floor alone would fill
    # all 25 slots and buy no reach at all.
    w = hires / hires.sum()
    var = (w[:, None] * (Pn - (w[:, None] * Pn).sum(0)) ** 2).sum(0)
    rank_n = [int(i) for i in np.argsort(-var)]

    def greedy_broad(base, k):
        picked, P = [], base
        while len(picked) < k:
            best, best_cov = None, -1
            for j in range(Pb.shape[1]):
                if j in picked:
                    continue
                c = coverage(np.hstack([P, Pb[:, [j]]]))
                if c > best_cov:
                    best, best_cov = j, c
            picked.append(best)
            P = np.hstack([P, Pb[:, [best]]])
        return picked, P

    # Pruning decides how many narrow items survive, and it is often fewer than
    # n_final. Sweeping down from n_final regardless silently shipped a 23-item
    # instrument when only 17 narrow existed, because the broad count was
    # computed from the requested split rather than the granted one.
    n_avail = min(len(rank_n), n_final)
    splits = ([min(n_narrow, n_avail)] if n_narrow
              else list(range(n_avail, max(n_avail - 16, 4) - 1, -2)))
    trials = []
    for n_narrow in splits:
        keep = rank_n[:n_narrow]
        n_broad = n_final - len(keep)          # granted, not requested
        picked, P = greedy_broad(Pn[:, keep], n_broad)
        trials.append({"n_narrow": len(keep), "keep": keep, "broad": picked,
                       "cov": coverage(P)})
        print(f"  {len(keep):>2} narrow + {n_broad:>2} broad: "
              f"proxy reach {trials[-1]['cov']:>4}")

    # The proxy only decides which splits are worth measuring. It is not good
    # enough to choose between them: profiles rated in separate calls predicted
    # similarity 0.140 for 17 + 8 that measured 0.066, and 2 collapses for
    # 13 + 12 that measured 4. So every split clearing the reach floor is rated
    # for real and compared on what it actually does. Responses are cached, so
    # this is paid once.
    eligible = [t for t in trials if t["cov"] >= REACH_FLOOR] or trials
    print(f"\n  {len(eligible)} of {len(trials)} splits clear the proxy reach floor "
          f"of {REACH_FLOOR}; rating each of them for real")

    for t_ in eligible:
        texts = ([narrow.text.iloc[i] for i in t_["keep"]]
                 + [broad.text.iloc[j] for j in t_["broad"]])
        P = rate_all(texts, facts, txt, cfg, mc)
        m = s5._score_instrument(P, hires, names, cfg)
        t_.update(P=P, texts=texts, sim=m["mean_similarity_top30"],
                  ties=len(m["unresolvable_twins"]), reach=coverage(P),
                  bad=collapses(P, facts, cfg, txt))
        print(f"    {t_['n_narrow']:>2} + {len(t_['broad']):>2}: "
              f"similarity {t_['sim']:.3f}  reach {t_['reach']:>4}  "
              f"ties {t_['ties']:>2}  collapses {t_['bad']}")

    # Ties between occupations that really are alike are not a defect. Ties
    # between occupations whose postings describe different work are.
    pick = min(eligible, key=lambda t: (t["bad"], t["sim"]))
    keep_n, chosen_b = pick["keep"], pick["broad"]
    texts, P = pick["texts"], pick["P"]
    origins = ["narrow"] * len(keep_n) + ["broad"] * len(chosen_b)
    print(f"\nselected {len(texts)} items: {len(keep_n)} narrow, {len(chosen_b)} broad "
          f"— similarity {pick['sim']:.3f}, reach {pick['reach']}, "
          f"{pick['bad']} collapses")

    emit(pd.DataFrame({"question_id": range(len(texts)), "text": texts,
                       "origin": origins}), "mixed_questions", "question_id")
    prof = pd.DataFrame(P, columns=[f"q{i}" for i in range(len(texts))])
    prof.insert(0, "series_name", names)
    prof.insert(0, "series", series)
    emit(prof, "mixed_profiles_all", "series")

    print()
    audit(DATA / "mixed_profiles_all.parquet")

