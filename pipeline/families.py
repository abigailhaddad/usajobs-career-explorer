"""Derive work families by asking which occupations are real alternatives.

Two earlier attempts and what they taught:

  TF-IDF on posting text  - clusters by announcement template and agency, and
                            dumps 42% of the catalogue (police, custodial
                            working and patent examining together) in one bucket.
                            Occupations in a family share meaning, not words.
  random batches of 12    - the model returned twelve singletons, correctly:
                            twelve occupations drawn at random from 302 are not
                            alternatives to each other. No information per call.

So: one call per occupation, with candidates SEEDED from text similarity so
plausible relatives are actually present, and a forced ranking rather than a
grouping so every call yields signal whether or not a family is there.

    Run as part of stage 5: python run.py --stages 5
"""
import json
import re
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from . import llm, s5_questions as s5

from .config import DATA
N_SEEDED, N_RANDOM = 14, 8

BOILER = ("""experience service meet position positions grade announcement date requirements
level applicants year qualifications closing following information qualify federal gs
application applicant resume veterans eligible duties education specialized series employment
job work will may must including include related various other located department support
office center section program direct filled apply management public services agency incumbent
responsible perform performs provide provides candidate vacancy appointment appointments
selected salary pay flyer vacancies notify notice interested dha solicitation utilizing recruit
appoint authority competitive usajobs opm nasa va usao justice army navy air force defense
homeland security treasury interior agriculture commerce labor edrp loan repayment""").split()


class Match(BaseModel):
    number: int = Field(description="The number of the occupation from the list")
    closeness: int = Field(description="3 = the same kind of work, 2 = clearly related, "
                                       "1 = loosely related")


class Alternatives(BaseModel):
    matches: List[Match] = Field(
        description="Up to 6 occupations from the list that are genuine alternatives. "
                    "Return an empty list if none of them are.")


SYSTEM = (
    "You judge which federal occupations are realistic alternatives to each other "
    "for one person deciding what to do for a living.\n\n"
    "Two occupations are alternatives when the day-to-day experience is similar: who "
    "you deal with, where the work happens, what you handle, what a mistake costs. "
    "Police and firefighting are alternatives — emergency response to the public, in "
    "uniform — though the words describing them differ. Custodial work and patent "
    "examining are not, whatever they share administratively.\n\n"
    "Ignore pay grade, agency, and how the job is advertised. Judge the work. Be "
    "willing to return nothing if none of the candidates genuinely fit.")


def run(limit=0):
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    llm.load_env(cfg["env_file"])
    mc = cfg["model"]

    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    txt = {r.series: json.loads(r.text_sample)
           for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}
    n = len(facts)

    def blurb(i, short=False):
        r = facts.iloc[i]
        bits = [r.series_name]
        if not short:
            if r.ce_description:
                bits.append(r.ce_description[:180])
            s = txt.get(r.series, [])
            if s:
                bits.append("posted as: " + "; ".join(d.get("title", "") for d in s[:2]))
        elif r.ce_description:
            bits.append(r.ce_description[:110])
        return " | ".join(b for b in bits if b)

    # Seed candidates from text similarity. It is too weak to define families on
    # its own but it is far better than chance at proposing who to compare.
    docs = [re.sub(r"\d+", " ", f"{facts.iloc[i].series_name}. "
                   f"{facts.iloc[i].ce_description or ''}") for i in range(n)]
    V = TfidfVectorizer(stop_words=list(ENGLISH_STOP_WORDS) + BOILER,
                        ngram_range=(1, 2), min_df=2, max_df=0.5, sublinear_tf=True)
    X = V.fit_transform(docs)
    X = np.asarray((X / np.maximum(np.sqrt(X.multiply(X).sum(1)), 1e-9)).todense())
    Tsim = X @ X.T
    np.fill_diagonal(Tsim, -1)

    rng = np.random.default_rng(0)
    targets = list(range(n))[: limit or n]
    print(f"{n} occupations; asking about {len(targets)}, "
          f"{N_SEEDED} seeded + {N_RANDOM} random candidates each")

    def ask(i):
        seeded = list(np.argsort(-Tsim[i])[:N_SEEDED])
        pool = [j for j in range(n) if j != i and j not in seeded]
        cand = seeded + list(rng.choice(pool, N_RANDOM, replace=False))
        rng.shuffle(cand)
        listing = "\n".join(f"{k}. {blurb(j, short=True)}" for k, j in enumerate(cand))
        res = llm.call(
            f"Occupation:\n{blurb(i)}\n\n"
            f"Which of these are realistic alternatives to it?\n\n{listing}",
            SYSTEM, Alternatives, mc["generate"], mc["temperature"],
            mc["timeout_seconds"], mc["max_retries"])
        return i, cand, res

    got = llm.map_concurrent(ask, targets, mc["max_concurrent"])
    S = np.zeros((n, n)); asked = np.zeros(n)
    fails = 0
    for i, cand, res in got:
        if res is None:
            fails += 1
            continue
        asked[i] = 1
        for mt in res.matches:
            if 0 <= mt.number < len(cand):
                j = cand[mt.number]
                w = max(0, min(3, mt.closeness)) / 3
                S[i, j] = max(S[i, j], w)
                S[j, i] = max(S[j, i], w)
    if fails > len(targets) * 0.1:
        raise RuntimeError(f"{fails} of {len(targets)} calls failed")
    print(f"  {int(asked.sum())} occupations answered, {fails} failed")
    print(f"  links found: {int((S > 0).sum() / 2):,} pairs "
          f"({100 * (S > 0).mean():.1f}% of the matrix)\n")
    np.save(DATA / "llm_similarity.npy", S)
    facts[["series", "series_name"]].to_csv(DATA / "llm_families_index.csv", index=False)

    # Cluster on similarity PROFILES: two occupations named alongside the same
    # others belong together even if never directly compared.
    Sr = S + np.eye(n)
    Sr = Sr / np.maximum(np.linalg.norm(Sr, axis=1, keepdims=True), 1e-9)
    D = 1 - Sr @ Sr.T
    np.fill_diagonal(D, 0)
    D[D < 0] = 0
    Z = linkage(squareform(D, checks=False), method="average")
    for k in (8, 12, 16):
        lab = fcluster(Z, k, criterion="maxclust")
        sizes = pd.Series(lab).value_counts()
        print(f"--- {k} families: sizes {sorted(sizes, reverse=True)} "
              f"(largest {100 * sizes.max() / n:.0f}%) ---")
        if k == 12:
            for c in sorted(set(lab), key=lambda c: -(lab == c).sum()):
                idx = np.where(lab == c)[0]
                top = facts.iloc[idx].nlargest(5, "hires_entry_perm").series_name.tolist()
                print(f"  {len(idx):3d} occ, "
                      f"{int(facts.iloc[idx].hires_entry_perm.sum()):7,} hires | "
                      f"{'; '.join(top)}")
            np.save(DATA / "llm_labels_12.npy", lab)
        print()

