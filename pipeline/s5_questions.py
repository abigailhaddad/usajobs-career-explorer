"""Stage 5 — generate a question set that separates the jobs we actually hire for.

Three steps, all re-runnable:
  1. GENERATE  candidate items from real occupations (titles + qualification facts)
  2. RATE      every target occupation against every candidate -> a profile matrix
  3. SCORE     the resulting instrument with the same maths used to grade OPM's,
               then prune redundant and non-discriminating items.

Step 3 is the point. We are not asking a model whether its questions are good;
we are measuring whether they separate occupations weighted by actual hiring.
"""
import json
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field

from . import llm
from .common import emit
from .config import DATA

CONFIG = Path(__file__).resolve().parent / "questions_config.yaml"


# ---- schemas --------------------------------------------------------------
class Candidate(BaseModel):
    text: str = Field(description="The item, written as a plain description of the activity in the imperative, like a job description. No question form, no question mark. One or two sentences.")
    axis: str = Field(description="Which separating axis this item is drawn from")
    separates: str = Field(description="Two occupations from the list that would answer this very differently")


class CandidateBatch(BaseModel):
    questions: List[Candidate]


class Rating(BaseModel):
    question_id: int
    score: int = Field(description="0 = not part of this job at all, 4 = central to it")


class RatingSet(BaseModel):
    ratings: List[Rating]


# ---- helpers --------------------------------------------------------------
def _occ_blurb(r, text_by_series=None) -> str:
    titles = json.loads(r.common_titles)[:4]
    bits = [f"{r.series_name} (series {r.series})"]
    if titles:
        bits.append("posted as: " + "; ".join(titles))
    if r.ce_description:
        bits.append(r.ce_description[:220])
    facts = []
    if r.pct_degree_required >= 25:
        facts.append(f"{r.pct_degree_required:.0f}% of entry postings require a degree")
    if r.pct_license_or_cert >= 50:
        facts.append(f"{r.pct_license_or_cert:.0f}% want a licence/certification")
    if r.pct_age_limit >= 25:
        facts.append("has a maximum entry age")
    if r.pct_clearance >= 40:
        facts.append(f"{r.pct_clearance:.0f}% need a clearance")
    if facts:
        bits.append("; ".join(facts))
    bits.append(f"~{r.hires_per_year:.0f} permanent entry hires/yr")
    out = " | ".join(bits)
    # Ground the model in what postings actually say rather than letting it rate
    # from the job title. Without this the whole instrument is one model's
    # impression of federal work, validated against itself.
    if text_by_series:
        sample = text_by_series.get(r.series, [])
        if sample:
            out += "\n  What real announcements say about this work:\n" + "\n".join(
                f"   - {d.get('title','')}: {(d.get('summary') or '')[:400]}"
                for d in sample[:2])
    return out


def _targets(df, cfg):
    t = cfg["targets"]
    m = df[df.hires_entry_perm >= t["min_entry_hires"]]
    if t.get("require_open_or_recent"):
        m = m[~m.status.isin(["never_reachable", "dormant"])]
    return m.nlargest(t["max_series"], "hires_entry_perm").reset_index(drop=True)


def _respondent_spread(P, n=3000, seed=0):
    """How much of the catalogue the instrument can actually recommend.

    The other half of the objective, and the half that was missing. Occupation
    separation alone is satisfiable by shrinking the instrument: a 6-item version
    scored better than the 21-item one while collapsing 5,000 takers onto just 32
    possible top matches. An instrument has to tell PEOPLE apart too.
    """
    sd = P.std(1, keepdims=True)
    sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    rng = np.random.default_rng(seed)
    R = rng.integers(0, 5, size=(n, P.shape[1])).astype(float)
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    top1 = np.argmax(z @ Pz.T / P.shape[1], axis=1)
    _, counts = np.unique(top1, return_counts=True)
    # Effective number of distinct recommendations, as a share of the catalogue.
    # Max-share is too blunt: shrinking 21 items to 6 moved it only 0.02 -> 0.08
    # while collapsing the reachable recommendations from 156 to 32.
    p = counts / counts.sum()
    effective = float(np.exp(-(p * np.log(p)).sum()))
    return effective / len(Pz)


COVERAGE_FLOOR = 0.95   # an item set may not lose more than 5% of reachable
                        # recommendations relative to the full candidate set


def _objective(P, hires, names, cfg, baseline_coverage=None):
    """Lower is better. Three terms, because the first two alone can be gamed:
    occupations should be distinguishable, pairs should not be tied, and
    respondents should not all land on the same recommendation."""
    m = _score_instrument(P, hires, names, cfg)
    # Coverage is a CONSTRAINT, not another weight. As a weighted term it was
    # gamed twice: first the objective preferred a 6-item instrument that
    # collapsed 5,000 takers onto 32 recommendations, and after reweighting it
    # still preferred a trim that cut coverage 156 -> 142. A weight I keep
    # retuning is the wrong tool; a floor cannot be traded away.
    if baseline_coverage is not None:
        cov = _respondent_spread(P)
        if cov < COVERAGE_FLOOR * baseline_coverage:
            return float("inf")
    return m["mean_similarity_top30"] + 0.02 * len(m["unresolvable_twins"])


def prune(P, cands, scored, cfg, n_final=None, redundancy_max=None, min_var=None):
    """Drop items that do not discriminate, then de-duplicate correlated ones.

    Kept separate from run() so a cached rating matrix can be re-pruned at
    different settings without spending any API calls.
    """
    sc = cfg["scoring"]
    n_final = n_final or cfg["generation"]["n_final"]
    redundancy_max = redundancy_max or sc["redundancy_max"]
    min_var = sc["min_hiring_weighted_var"] if min_var is None else min_var
    var = scored["hiring_weighted_var"]

    keep = [i for i in range(len(cands)) if var[i] >= min_var]
    C = np.corrcoef(P.T)
    pruned = []
    for i in list(keep):
        for j in keep:
            if j >= i or j in pruned or i in pruned:
                continue
            if abs(C[i, j]) >= redundancy_max:
                pruned.append(i if var[i] < var[j] else j)
    keep = [i for i in keep if i not in pruned]

    # Keep at least one item per axis, or the pruner drops whole dimensions.
    by_axis = {}
    for i in range(len(cands)):
        by_axis.setdefault(cands[i].axis.strip().lower()[:40], []).append(i)
    for idxs in by_axis.values():
        if not any(i in keep for i in idxs):
            keep.append(max(idxs, key=lambda i: var[i]))
    return sorted(keep, key=lambda i: -var[i])[:n_final]


def _score_instrument(P, hires, names, cfg):
    """One scoring function for every instrument, so they stay comparable."""
    w = hires / hires.sum()
    wm = (w[:, None] * P).sum(0)
    wv = (w[:, None] * (P - wm) ** 2).sum(0)
    sd = P.std(1, keepdims=True)
    sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    S = Pz @ Pz.T / P.shape[1]
    top = np.argsort(-hires)[:30]
    sub = S[np.ix_(top, top)]
    twins = []
    thr = cfg["scoring"]["report_twin_threshold"]
    for a in range(len(top)):
        for b in range(a + 1, len(top)):
            if sub[a, b] >= thr:
                twins.append((names[top[a]], names[top[b]], round(float(sub[a, b]), 3)))
    return {
        "hiring_weighted_var": wv,
        "mean_similarity_top30": float(sub[~np.eye(len(top), dtype=bool)].mean()),
        "unresolvable_twins": twins,
    }


# ---- stage ----------------------------------------------------------------
def run(limit_series: int | None = None, n_candidates: int | None = None,
        verbose: bool = True):
    cfg = yaml.safe_load(CONFIG.read_text())
    llm.load_env(cfg["env_file"])
    mc, gen = cfg["model"], cfg["generation"]
    if n_candidates:
        gen["n_candidates"] = n_candidates

    facts = pd.read_parquet(DATA / "series_facts.parquet")
    tp = DATA / "series_text.parquet"
    text_by_series = {}
    if tp.exists():
        td = pd.read_parquet(tp)
        text_by_series = {r.series: json.loads(r.text_sample) for r in td.itertuples()}
        print(f"  grounding ratings in posting text for {len(text_by_series)} series")
    else:
        print("  !! data/series_text.parquet missing — rating from titles only")
    tgt = _targets(facts, cfg)
    if limit_series:
        tgt = tgt.head(limit_series)
    print(f"stage 5: {len(tgt)} target occupations "
          f"({int(tgt.hires_entry_perm.sum()):,} permanent entry hires)")

    def rate_all(cands, tag, rows=None):
        qlist = "\n".join(f"{i}. {c.text}" for i, c in enumerate(cands))
        def rate_one(r):
            out = llm.call(
                f"Occupation:\n{_occ_blurb(r, text_by_series)}\n\n"
                f"Score every statement 0-{cfg['rating']['scale_max']}. "
                f"Return one entry per statement id.\n\n{qlist}",
                cfg["rating"]["prompt"], RatingSet, mc["rate"], mc["temperature"],
                mc["timeout_seconds"], mc["max_retries"], verbose)
            row = np.full(len(cands), np.nan)
            if out:
                for rt in out.ratings:
                    if 0 <= rt.question_id < len(cands):
                        row[rt.question_id] = max(0, min(cfg["rating"]["scale_max"], rt.score))
            return row
        M = np.vstack(llm.map_concurrent(rate_one, rows or list(tgt.itertuples()),
                                         mc["max_concurrent"]))
        miss = np.isnan(M).mean()
        if miss > 0.25:
            raise RuntimeError(f"{tag}: too many unrated cells ({miss:.0%})")
        return np.where(np.isnan(M), np.nanmean(M, axis=0), M)

    # --- 1. generate ------------------------------------------------------
    axes = "\n".join(f"- {a}" for a in gen["axes"])
    system = (
        "You write items for a career-matching instrument for federal jobs. "
        "The existing instrument fails because its items do not separate the "
        "occupations that actually hire: its 30 biggest entry-level hirers sit at "
        "0.19 mean profile similarity, and pairs like criminal investigating vs "
        "customs interdiction are indistinguishable to it even though one hires "
        "thousands and the other hires nobody. Your items must separate real jobs.\n\n"
        f"Draw items from these axes:\n{axes}\n\n{gen['style']}\n\n{gen['avoid']}"
    )
    batches = [tgt.iloc[i:i + gen["batch_size"]]
               for i in range(0, len(tgt), gen["batch_size"])]
    per = max(3, gen["n_candidates"] // max(len(batches), 1))

    hires = tgt.hires_entry_perm.to_numpy(float)
    names = tgt.series_name.tolist()

    def gen_sample(sample_idx):
        def gen_one(b):
            listing = "\n".join(f"- {_occ_blurb(r, text_by_series)}" for r in b.itertuples())
            return llm.call(
                f"Here are real federal occupations that hire at entry level:\n\n{listing}\n\n"
                f"Write {per} items that would separate these occupations from each other.\n"
                f"(Independent attempt {sample_idx + 1}: write a different set from what an "
                f"obvious first pass would produce.)",
                system, CandidateBatch, mc["generate"], mc["temperature"],
                mc["timeout_seconds"], mc["max_retries"], verbose)
        got = llm.map_concurrent(gen_one, batches, mc["max_concurrent"])
        return [c for g in got if g for c in g.questions][:gen["n_candidates"]]

    samples = []
    for si in range(gen.get("n_samples", 1)):
        cs = gen_sample(si)
        if not cs:
            continue
        M = rate_all(cs, f"sample {si + 1}")
        obj = _objective(M, hires, names, cfg)
        samples.append((obj, cs, M))
        print(f"  sample {si + 1}: {len(cs)} candidates, objective {obj:+.3f}")
    if not samples:
        raise RuntimeError("no candidate questions were generated")
    samples.sort(key=lambda t: t[0])
    _, cands, P = samples[0]
    print(f"  best of {len(samples)} samples: objective {samples[0][0]:+.3f} "
          f"(worst {samples[-1][0]:+.3f})")
    # Baseline: OPM's own 32 items, scored on exactly these occupations, so the
    # comparison is like-for-like rather than against its full 302-series number.
    opm = _score_instrument(np.array([json.loads(p) for p in tgt.profile]),
                            hires, names, cfg)
    before = _score_instrument(P, hires, names, cfg)

    keep = prune(P, cands, before, cfg)

    # --- 3b. residual rounds ---------------------------------------------
    # Rather than showing the model more occupations at once, show it the ones it
    # is currently failing on. Each round is kept only if the objective improves,
    # so a round that does not help costs money but cannot damage the instrument.
    for rnd in range(gen.get("residual_rounds", 0)):
        cur = _score_instrument(P[:, keep], hires, names, cfg)
        twins = cur["unresolvable_twins"]
        if not twins:
            print(f"  residual round {rnd + 1}: nothing left unresolved")
            break
        pairs = "\n".join(f"- {a} vs {b} (similarity {c})" for a, b, c in twins[:8])
        blurbs = {n: _occ_blurb(r, text_by_series) for n, r in zip(names, tgt.itertuples())}
        detail = "\n".join(f"- {blurbs[n]}" for n in
                            dict.fromkeys([x for t in twins[:8] for x in t[:2]]))
        out = llm.call(
            f"This instrument still cannot tell these occupation pairs apart:\n{pairs}\n\n"
            f"Details on those occupations:\n{detail}\n\n"
            f"Write {gen['residual_items_per_round']} items that would score these "
            f"specific pairs DIFFERENTLY from each other. Each item must split at "
            f"least one named pair. Do not restate items about what they have in common.",
            system, CandidateBatch, mc["generate"], mc["temperature"],
            mc["timeout_seconds"], mc["max_retries"], verbose)
        if not out or not out.questions:
            print(f"  residual round {rnd + 1}: no items returned")
            break
        extra = out.questions[:gen["residual_items_per_round"]]
        Mx = rate_all(list(cands) + list(extra), f"residual {rnd + 1}")
        widened = list(range(len(cands), len(cands) + len(extra)))
        cand_keep = keep + widened
        before_obj = _objective(P[:, keep], hires, names, cfg)
        after_obj = _objective(Mx[:, cand_keep], hires, names, cfg)
        if after_obj < before_obj:
            print(f"  residual round {rnd + 1}: +{len(extra)} items, "
                  f"objective {before_obj:+.3f} -> {after_obj:+.3f} (kept)")
            cands = list(cands) + list(extra)
            P, keep = Mx, cand_keep
        else:
            print(f"  residual round {rnd + 1}: objective {before_obj:+.3f} -> "
                  f"{after_obj:+.3f} (no improvement, discarded)")
            break

    after = _score_instrument(P[:, keep], hires, names, cfg)

    print(f"\n  candidates {len(cands)} -> kept {len(keep)}")
    print("  mean profile similarity on the SAME occupations (lower = separates better):")
    print(f"    OPM's 32 items      : {opm['mean_similarity_top30']:+.3f}"
          f"   ({len(opm['unresolvable_twins'])} pairs it cannot separate)")
    print(f"    generated, pruned   : {after['mean_similarity_top30']:+.3f}"
          f"   ({len(after['unresolvable_twins'])} pairs)")

    final = _score_instrument(P[:, keep], hires, names, cfg)
    # Score every series, not just the targets: the site ranks all 302, and a
    # series with no profile simply cannot appear in anyone's results.
    all_rows = list(facts.itertuples())
    print(f"  rating the full catalogue: {len(all_rows)} series x {len(keep)} items")
    kept_cands = [cands[i] for i in keep]
    P_all = rate_all(kept_cands, "full", rows=all_rows)
    prof_all = pd.DataFrame(P_all, columns=[f"q{n}" for n in range(len(keep))])
    prof_all.insert(0, "series_name", facts.series_name.tolist())
    prof_all.insert(0, "series", facts.series.tolist())
    emit(prof_all, "generated_profiles_all", "series")

    qdf = pd.DataFrame([{
        "question_id": n, "text": cands[i].text, "axis": cands[i].axis,
        "separates": cands[i].separates,
        "hiring_weighted_var": round(float(final["hiring_weighted_var"][k]), 3),
    } for n, (k, i) in enumerate(enumerate(keep))])
    emit(qdf, "generated_questions", "question_id")
    prof = pd.DataFrame(P[:, keep], columns=[f"q{n}" for n in range(len(keep))])
    prof.insert(0, "series_name", names)
    prof.insert(0, "series", tgt.series.tolist())
    emit(prof, "generated_profiles", "series")
    (DATA / "generated_questions_report.json").write_text(json.dumps({
        "n_targets": len(tgt), "n_candidates": len(cands), "n_kept": len(keep),
        "mean_similarity_opm_same_targets": opm["mean_similarity_top30"],
        "opm_unresolvable_twins": opm["unresolvable_twins"][:20],
        "mean_similarity_before": before["mean_similarity_top30"],
        "mean_similarity_after": after["mean_similarity_top30"],
        "unresolvable_twins_after": after["unresolvable_twins"][:20],
    }, indent=2))
    return qdf
