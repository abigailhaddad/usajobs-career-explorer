"""Stage 4 — join into one honest fact table, and write the site's data.json.

All the expensive work happened upstream; this stage is pure joins over ~300-row
tables. Qualification percentages and common titles arrive from stage 2, where
they were computed inside DuckDB without materialising any posting text.
"""
import json
import urllib.parse
from datetime import date

import numpy as np
import pandas as pd

from .common import emit, pct, titlecase
from .config import CURRENT_JOBS_YEARS, DATA, HIRE_MONTHS, SITE


def _flags(r):
    """The fine print the official tool leaves off. Order = severity."""
    f = []
    if r.status == "never_reachable":
        f.append(("no_open_door",
                  f"Has not posted an entry-level job anyone could apply to "
                  f"since January {CURRENT_JOBS_YEARS[0]}"))
    elif r.status == "dormant":
        f.append(("dormant",
                  f"Nothing anyone could apply to for {r.months_since_reachable:.0f} months"))
    elif r.status == "recently_active":
        f.append(("nothing_open_now", "Nothing open right now, but it posts from time to time"))
    if r.hires_per_year >= 100 and r.ann_reachable <= 5:
        f.append(("bulk_hiring", "Hires in bulk waves rather than ordinary postings — easy to miss"))
    if r.hires_per_year < 50:
        f.append(("thin", "Almost no permanent entry hires, governmentwide"
                  if r.hires_per_year < 1 else
                  f"About {r.hires_per_year:.0f} permanent entry hires a year, governmentwide"))
    # OPM's own standard beats what postings happen to restate. Most
    # announcements never mention the Individual Occupational Requirement:
    # computer science requires a degree absolutely and its postings scored 5%.
    if r.degree_requirement == "degree":
        f.append(("degree_required", "Needs a bachelor's degree"))
    elif r.degree_requirement == "credential":
        f.append(("credential_required",
                  "Needs a licence or certificate, though not usually a degree"))
    # Threshold calibrated, not guessed. "maximum entry age" is a specific phrase
    # nobody writes incidentally, and most postings for a capped occupation never
    # restate the cap — so the base rate is low and a 25% bar produced false
    # negatives on genuinely age-capped work (correctional officer 17.9%,
    # police 6.2%). A low floor on a high-specificity phrase is the right shape.
    if r.pct_age_limit >= 5:
        f.append(("age_cap", "Federal law enforcement and firefighting have a maximum "
                             "entry age, usually 37"))
    if r.young_hires_per_year < 20 and r.hires_per_year >= 100:
        f.append(("skews_older",
                  f"Hires {r.hires_per_year:.0f} people a year at entry level but only "
                  f"{r.young_hires_per_year:.0f} of them are under 30"))
    # Outcome, not just opportunity: a big hirer people leave fast is not a
    # good recommendation, however well it matches your interests.
    return f


def run():
    print("stage 4: build fact table")
    df = (pd.read_parquet(DATA / "series_profiles.parquet")
            .merge(pd.read_parquet(DATA / "openings.parquet"), on="series", how="left")
            .merge(pd.read_parquet(DATA / "hires.parquet"), on="series", how="left"))
    sp = DATA / "opm_standards.parquet"
    if sp.exists():
        df = df.merge(pd.read_parquet(sp)[["series", "opm_degree_required"]],
                      on="series", how="left")
    else:
        df["opm_degree_required"] = pd.NA

    num = [c for c in df.columns if df[c].dtype.kind in "if"]
    df[num] = df[num].fillna(0)
    df["status"] = df.status.fillna("never_reachable")
    df["common_titles"] = df.common_titles.fillna("[]")
    df["live_jobs"] = df.live_jobs.fillna("[]")
    df["openings_by_state"] = df.openings_by_state.fillna("{}")

    # The two sources spell locations differently: postings say "Ohio", the OPM
    # accession records say "GEORGIA". Left alone the state filter silently
    # matches nothing across sources. Non-locations are dropped outright.
    NOT_A_PLACE = {"no data reported", "unspecified", "", "none"}

    def norm_states(js):
        out = {}
        for k, v in json.loads(js).items():
            key = " ".join(w.capitalize() for w in str(k).strip().split())
            if key.lower() in NOT_A_PLACE:
                continue
            out[key] = out.get(key, 0) + int(v)
        return json.dumps(out)

    df["openings_by_state"] = df.openings_by_state.map(norm_states)

    # --- hiring trend ------------------------------------------------------
    # Trailing 12 months against the best 12-month window in the record.
    # Monthly, and like-for-like: the data ends mid-year, so comparing the
    # latest calendar year against full-year peaks halves every ratio and
    # manufactures a collapse that is partly just a short year.
    yp = DATA / "hires_by_month.parquet"
    if yp.exists():
        y = pd.read_parquet(yp)
        wide = (y.pivot_table(index="series", columns="month", values="entry_hires",
                              aggfunc="sum", fill_value=0)
                 .reindex(df.series).fillna(0))
        months = sorted(wide.columns)
        last12 = wide[months[-12:]].sum(axis=1).to_numpy()
        # best full 12-month window, so the comparison is 12 months vs 12 months
        best12 = np.max([wide[months[i:i + 12]].sum(axis=1).to_numpy()
                         for i in range(len(months) - 11)], axis=0)
        df["window_end"] = months[-1]
        df["hires_last12"] = last12.astype(int)
        df["hires_best12"] = best12.astype(int)
        df["trend_vs_peak"] = pct(last12, best12)
        by_year = (y.assign(year=y.month.str[:4])
                    .pivot_table(index="series", columns="year", values="entry_hires",
                                 aggfunc="sum", fill_value=0)
                    .reindex(df.series).fillna(0).astype(int))
        df["hires_by_year"] = [json.dumps({str(k): int(v) for k, v in r.items()})
                               for _, r in by_year.iterrows()]
        hire_years = len(months) / 12
        print(f"  hiring trend over {len(months)} months, "
              f"trailing 12 ends {months[-1]}")
    else:
        print("  !! data/hires_by_month.parquet missing — run stage 3")
        for c, v in (("window_end", ""), ("hires_last12", 0), ("hires_best12", 0),
                     ("trend_vs_peak", np.nan), ("hires_by_year", "{}")):
            df[c] = v
        hire_years = 5

    # Top states for permanent entry hiring, so a result can say where this work
    # actually is even when nothing is posted there today.
    gp = DATA / "hires_by_state.parquet"
    if gp.exists():
        # Every state, not the top few. Capping made a card read "0 entry hires
        # there" for a state that simply was not in the top six — which is a
        # different and much more discouraging claim than the truth.
        g = pd.read_parquet(gp).sort_values("entry_hires", ascending=False)
        top = (g.groupby("series")
                .apply(lambda x: json.dumps(dict(zip(x.state, x.entry_hires.astype(int)))),
                       include_groups=False).rename("hires_by_state").reset_index())
        df = df.merge(top, on="series", how="left")
        df["hires_by_state"] = df.hires_by_state.fillna("{}").map(norm_states)
    else:
        print("  !! data/hires_by_state.parquet missing — run stage 3")
        df["hires_by_state"] = "{}"

    n_loc = len({k for c in ("openings_by_state", "hires_by_state")
                 for js in df[c] for k in json.loads(js)})
    print(f"  {n_loc} distinct locations after normalising both sources")

    df["pct_entry_young"] = pct(df.hires_entry_perm_young, df.hires_entry_perm)
    df["pct_entry_temp"] = pct(df.hires_entry_temp, df.hires_entry_temp + df.hires_entry_perm)
    # The hire window is open-ended (HIRE_MONTHS end = None), so the divisor
    # has to track the months actually covered; a fixed 5 was already off by
    # half a year and drifting further every month.
    df["hires_per_year"] = (df.hires_entry_perm / hire_years).round(0)
    # Counts, not just shares. A share tells you the composition of a job; a
    # young person wants to know how many people like them actually got hired.
    df["young_hires_per_year"] = (df.hires_entry_perm_young / hire_years).round(0)
    df["young_hires_total"] = df.hires_entry_perm_young.astype(int)

    # What people hired into this actually held, kept in the data for reference
    # but not shown: the requirement is the actionable part.
    df["pct_degree_held"] = pct(df.edu_bachelors_plus, df.edu_reported)
    df["pct_college_held"] = pct(df.edu_any_college, df.edu_reported)
    # Fallback for occupations barely entered at entry grade.
    df["pct_degree_any"] = pct(df.edu_bachelors_plus_any, df.edu_reported_any)
    df["pct_college_any"] = pct(df.edu_any_college_any, df.edu_reported_any)

    # Only the requirement matters. Whether 20% or 60% of hires happened to hold
    # a degree is not something a person can act on; whether they need one is.
    # Three states, and two of them are worth saying.
    #
    # The two sources fail in opposite directions, so take the union:
    #   OPM's standard: precise, poor recall — engineering series only point at
    #     a group standard ("use the GS-800 requirements"), which the parser
    #     cannot follow, so mechanical engineering reads as no requirement.
    #   What hires held: complete, but ~6% of the education field is miscoded.
    #     Occupations where a degree is unavoidable top out at 92-98%, never
    #     100%, and economist reads 88% against an absolute requirement.
    def requirement(p, n, opm, col, p_any, n_any, col_any):
        if opm is True:
            return "degree"
        # Thin at entry grade? Read the credential off whoever they do hire.
        # General attorney has 16 entry hires and 20,451 new hires, all with a
        # JD; "too few to say" was hiding an answer we plainly have.
        if n < 30 and n_any >= 30:
            p, n, col = p_any, n_any, col_any
        if n < 30 or pd.isna(p):
            return "unknown"
        if p >= 90:
            return "degree"
        # Licensed sub-baccalaureate work: practical nurse is 9% bachelor's but
        # 87% some college, because an LPN qualifies on a diploma. Saying "no
        # degree needed" there is wrong about the job.
        if col is not None and col >= 85 and p < 65:
            return "credential"
        return "none"

    # Whether the job is a permanent one. Same reasoning as the education
    # field: a label, not a percentage — the useful question is whether you are
    # being hired into a career or a season, not whether it is 31% or 44%.
    def tenure_kind(t):
        if pd.isna(t):
            return "unknown"
        if t >= 50:
            return "usually temporary"
        if t >= 20:
            return "mixed"
        return "usually permanent"

    df["tenure_kind"] = [tenure_kind(t) for t in df.pct_entry_temp]

    df["degree_requirement"] = [
        requirement(p, n, o, c, pa, na, ca) for p, n, o, c, pa, na, ca in
        zip(df.pct_degree_held, df.edu_reported, df.opm_degree_required,
            df.pct_college_held, df.pct_degree_any, df.edu_reported_any,
            df.pct_college_any)]

    df["job_url"] = ("https://www.usajobs.gov/Search/Results?k="
                     + df.series_name.map(urllib.parse.quote_plus))

    fl = df.apply(_flags, axis=1)
    df["flags"] = [json.dumps([{"code": c, "note": n} for c, n in f]) for f in fl]
    df["flag_count"] = [len(f) for f in fl]
    emit(df, "series_facts", "series")

    clean = int((df.flag_count == 0).sum())
    print(f"  {clean} of {len(df)} series carry no warning flag")
    print(f"  open to the public at entry grade right now: {int((df.reachable_open_now > 0).sum())}")

    # ---- site payload -----------------------------------------------------
    SITE.mkdir(parents=True, exist_ok=True)
    # The site uses the generated instrument when stage 5 has produced one for
    # the whole catalogue, and falls back to OPM's 32 items otherwise.
    # The mixed set (narrow items for separation + broad items so answers land
    # somewhere) is preferred when present. Measured over three rating draws it
    # cuts unresolvable pairs 20-22 -> 5-9 and raises reachable recommendations
    # 142-156 -> 210-211, and leaves only one of twelve work families with
    # nothing to express interest on, against three for the narrow-only set.
    # It is worse on mean similarity, which the broad items inflate by design.
    mq, mp = DATA / "mixed_questions.parquet", DATA / "mixed_profiles_all.parquet"
    gq, gp = ((mq, mp) if mq.exists() and mp.exists()
              else (DATA / "generated_questions.parquet",
                    DATA / "generated_profiles_all.parquet"))
    use_generated = gq.exists() and gp.exists()
    if use_generated:
        q = pd.read_parquet(gq)
        prof = pd.read_parquet(gp).set_index("series")
        qcols = [c for c in prof.columns if c.startswith("q") and c[1:].isdigit()]
        qcols.sort(key=lambda c: int(c[1:]))
        missing = set(df.series) - set(prof.index)
        if missing:
            raise RuntimeError(f"{len(missing)} series have no generated profile "
                               f"(e.g. {sorted(missing)[:5]}) — re-run stage 5")
        questions = [{"question_id": int(r.question_id), "question_text": r.text}
                     for r in q.itertuples()]
        profiles = {s_: [float(v) for v in prof.loc[s_, qcols]] for s_ in df.series}
        print(f"  site instrument: {len(questions)} generated questions")
    else:
        qs = pd.read_parquet(DATA / "questions.parquet")
        questions = qs.rename(columns={"question_text": "question_text"})[
            ["question_id", "question_text"]].to_dict("records")
        profiles = {r.series: json.loads(r.profile) for r in df.itertuples()}
        print(f"  site instrument: OPM's {len(questions)} questions (stage 5 not run)")
    gov = {}
    if yp.exists():
        m = y.groupby("month").entry_hires.sum().sort_index()
        mo = list(m.index)
        if len(mo) >= 12:
            wins = [int(m[mo[i:i + 12]].sum()) for i in range(len(mo) - 11)]
            gov = {"last12": wins[-1],
                   "last12_start": mo[-12], "last12_end": mo[-1]}

    payload = {
        "built": date.today().isoformat(),
        "governmentwide": gov,
        # Derived from the data, not from config: the window end is open-ended
        # so a re-run picks up new months.
        "hire_window": f"{HIRE_MONTHS[0][:4]}–{(df.window_end.max() or '')[:4]}",
        "postings_through": str(pd.to_datetime(df.last_seen_open).max().date()),
        "instrument": "generated" if use_generated else "opm",
        "questions": questions,
        "series": [{
            "series": r.series, "series_name": r.series_name,
            "profile": profiles[r.series],
            "ce_description": r.ce_description,
            "common_titles": [titlecase(x) for x in json.loads(r.common_titles)],
            "live_jobs": [{**j, "title": titlecase(j["title"])}
                          for j in json.loads(r.live_jobs)],
            "flags": json.loads(r.flags),
            "status": r.status,
            "hires_entry_perm": int(r.hires_entry_perm),
            "hires_per_year": int(r.hires_per_year),
            "young_hires_per_year": int(r.young_hires_per_year),
            "young_hires_total": int(r.young_hires_total),
            "hires_last12": int(r.hires_last12),
            "trend_vs_peak": None if pd.isna(r.trend_vs_peak) else float(r.trend_vs_peak),
            "hires_by_year": json.loads(r.hires_by_year),
            "pct_entry_young": None if pd.isna(r.pct_entry_young) else float(r.pct_entry_young),
            "reachable_open_now": int(r.reachable_open_now),
            "openings_open_now": int(r.openings_open_now),
            "typical_entry_grade": None if pd.isna(r.typical_entry_grade) else int(r.typical_entry_grade),
            "pct_degree_required": float(r.pct_degree_required),
            "degree_requirement": r.degree_requirement,
            "tenure_kind": r.tenure_kind,
            "pct_degree_held": None if pd.isna(r.pct_degree_held) else float(r.pct_degree_held),
            "pct_college_held": None if pd.isna(r.pct_college_held) else float(r.pct_college_held),
            "edu_reported": int(r.edu_reported),
            "opm_degree_required": (None if pd.isna(r.opm_degree_required)
                                    else bool(r.opm_degree_required)),
            "pct_education_substitutable": float(r.pct_education_substitutable),
            "pct_specialized_experience": float(r.pct_specialized_experience),
            "pct_license_or_cert": float(r.pct_license_or_cert),
            "pct_clearance": float(r.pct_clearance),
            "pct_age_limit": float(r.pct_age_limit),
            "job_url": r.job_url,
        } for r in df.itertuples()],
    }
    out = SITE / "data.json"
    out.write_text(json.dumps(payload, separators=(",", ":")))

    # Stamp app.js/style.css with their mtimes so a rebuilt site is not served
    # from a stale browser cache. A cached app.js against fresh data.json throws
    # errors whose line numbers point at code that is no longer there.
    import os
    import re as _re
    idx = SITE / "index.html"
    html = idx.read_text()
    for asset in ("app.js", "style.css"):
        v = int(os.path.getmtime(SITE / asset))
        html = _re.sub(rf'{_re.escape(asset)}(\?v=\d+)?', f"{asset}?v={v}", html)
    idx.write_text(html)
    print(f"  wrote site/data.json ({out.stat().st_size/1024:.0f} KB)")
    return df
