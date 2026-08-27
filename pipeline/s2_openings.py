"""Stage 2 — announcements per series, from the R2 current_jobs parquets.

Memory discipline (this stage used to blow up): nothing is ever materialised.
Three streaming aggregates, each grouping straight to ~300 rows.

  A. counts    — top-level columns only; never reads MatchedObjectDescriptor,
                 which holds a ~15 KB JSON blob per row.
  B. quals     — reads the descriptor but does the regex in SQL, so the text
                 is scanned and dropped rather than pulled into a table.
  C. titles    — top-level positionTitle only.

Update policy: a series is NEVER dropped because nothing is posted today.
Federal hiring is lumpy — tax examining posts in filing season, forestry
technician in spring, patent examining in occasional bulk waves. So counts
inside the window are recomputed from source, but `first_seen_open` /
`last_seen_open` are monotonic (carried forward from the previous run when the
window no longer reaches them), `months_active` accumulates the calendar months
this series has ever been seen posting in, and `status` derives from recency
rather than from presence today.
"""
import json
import re

import duckdb
import pandas as pd

from .common import emit
from .config import (CURRENT_JOBS_YEARS, DATA, ENTRY_MAX_GRADE, GRAD_PATHS, GS_LIKE,
                     OUTSIDER_PATHS, R2_BASE, TRADE_PLANS)

DORMANT_AFTER_MONTHS = 18
D = "MatchedObjectDescriptor"


def _lst(vals):
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in vals)


def _connect():
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # Keep this stage inside a small envelope: the parquets are remote and wide,
    # and the machine this runs on has been tight on disk.
    con.execute("SET enable_progress_bar=false; SET preserve_insertion_order=false;")
    con.execute("SET threads=4; SET memory_limit='2GB'; SET max_temp_directory_size='1GiB';")
    con.execute(f"SET temp_directory='{DATA}/.duckdb_tmp';")
    return con


def run():
    print("stage 2: announcements (R2 current_jobs)")
    urls = _lst(f"{R2_BASE}/current_jobs_{y}.parquet" for y in CURRENT_JOBS_YEARS)
    con = _connect()

    outsider = (f"EXISTS (SELECT 1 FROM json_each(HiringPaths) h "
                f"WHERE json_extract_string(h.value,'$.hiringPath') IN ({_lst(OUTSIDER_PATHS)}))")
    gradpath = (f"EXISTS (SELECT 1 FROM json_each(HiringPaths) h "
                f"WHERE json_extract_string(h.value,'$.hiringPath') IN ({_lst(GRAD_PATHS)}))")
    series = "json_extract_string(s.value,'$.series')"
    grade = "TRY_CAST(minimumGrade AS INT)"
    # A grade number only means "junior" on GS-style plans. Banded plans number
    # the other way: an IP-01 Deputy Director pays $151k, and 575 Senior
    # Executive Service postings were being counted as entry level because ES
    # grades read 01. Match the hires-side definition exactly — GS-style plans
    # at grades 1-9, plus wage-grade trades, which have no GS grade at all.
    gs_like = ",".join("'" + p + "'" for p in GS_LIKE)
    trades = ",".join("'" + p + "'" for p in TRADE_PLANS)
    entry_grade = (f"((payScale IN ({gs_like}) AND {grade} IS NOT NULL "
                   f"AND {grade} BETWEEN 1 AND {ENTRY_MAX_GRADE})"
                   f" OR payScale IN ({trades}))")
    reach = f"({outsider}) AND appointmentType='Permanent' AND {entry_grade}"
    openings = "COALESCE(TRY_CAST(totalOpenings AS INT),1)"

    # --- A. counts (no descriptor column touched) --------------------------
    counts = con.execute(f"""
    SELECT {series} AS series,
      count(*)                                        AS ann_total,
      count(*) FILTER ({outsider})                    AS ann_outsider,
      count(*) FILTER ({gradpath})                    AS ann_gradpath,
      count(*) FILTER ({reach})                       AS ann_reachable,
      COALESCE(sum({openings}) FILTER ({reach}),0)    AS openings_reachable,
      count(*) FILTER (CAST(positionCloseDate AS TIMESTAMP) > now())           AS open_now,
      count(*) FILTER (CAST(positionCloseDate AS TIMESTAMP) > now() AND {reach}) AS reachable_open_now,
      COALESCE(sum({openings}) FILTER (CAST(positionCloseDate AS TIMESTAMP) > now() AND {reach}),0)
                                                      AS openings_open_now,
      min(CAST(positionOpenDate AS TIMESTAMP)) FILTER ({reach}) AS first_seen_open,
      max(CAST(positionOpenDate AS TIMESTAMP)) FILTER ({reach}) AS last_seen_open,
      median({grade}) FILTER ({reach})                AS typical_entry_grade,
      list(DISTINCT month(CAST(positionOpenDate AS TIMESTAMP))) FILTER ({reach}) AS months_seen
    FROM read_parquet([{urls}]), json_each(JobCategories) AS s
    GROUP BY 1""").df()
    print(f"  A. counts: {len(counts):,} series")

    # --- B. qualification flags, regex evaluated in SQL ---------------------
    # Patterns and their negation guards both live in pipeline/quals.py;
    # sql_expr builds the boolean, including the `unless` clause.
    from .quals import PATTERNS, sql_expr
    flag_sql = ",\n      ".join(
        f"round(100.0*avg(CASE WHEN {sql_expr(name)} THEN 1 ELSE 0 END),1) AS pct_{name}"
        for name in PATTERNS)
    quals = con.execute(f"""
    SELECT series, count(*) AS postings_scanned,
      {flag_sql}
    FROM (
      SELECT {series} AS series,
             lower(COALESCE(json_extract_string({D},'$.QualificationSummary'),'') || ' ' ||
                   COALESCE(json_extract_string({D},'$.UserArea.Details.Education'),'')) AS txt
      FROM read_parquet([{urls}]), json_each(JobCategories) AS s
      WHERE {reach}
    ) GROUP BY series""").df()
    print(f"  B. qualification text: {len(quals):,} series scanned")

    # --- C. what these jobs are actually called ----------------------------
    # Entry-level postings only. Drawing from every posting open to the public
    # listed senior titles ("Supervisory ...", "Chief ...") for occupations
    # someone would enter near the bottom, which is not what the card is for.
    titles = con.execute(f"""
    SELECT {series} AS series, positionTitle AS title, count(*) AS c
    FROM read_parquet([{urls}]), json_each(JobCategories) AS s
    WHERE {reach}
    GROUP BY 1, 2""").df()
    # --- F. where the open jobs actually are -------------------------------
    # The state array is projected out BEFORE the row expands. The naive lateral
    # join over PositionLocation carries the 15 KB descriptor into every location
    # row (163k postings x ~20 locations) and fills the disk.
    geo = con.execute(f"""
    WITH x AS (
      SELECT {series} AS series,
             from_json(json_extract_string({D}, '$.PositionLocation[*].CountrySubDivisionCode'),
                       '["VARCHAR"]') AS states
      FROM read_parquet([{urls}]), json_each(JobCategories) AS s
      WHERE ({reach}) AND CAST(positionCloseDate AS TIMESTAMP) > now()
    ), y AS (SELECT series, unnest(states) AS state FROM x WHERE states IS NOT NULL)
    SELECT series, state, count(*) AS openings FROM y GROUP BY 1, 2""").df()
    geo = (geo.groupby("series")
              .apply(lambda g: json.dumps(dict(zip(g.state, g.openings.astype(int)))),
                     include_groups=False)
              .rename("openings_by_state").reset_index())
    print(f"  F. locations: {len(geo):,} series with open jobs placed")

    # --- E. a small, capped sample of real posting text per series ---------
    # Stage 5 rates occupations from this rather than from a job title, so the
    # ratings are grounded in what postings say instead of the model's
    # impression of the title. Capped hard: 3 postings, truncated, per series.
    text = con.execute(f"""
    SELECT series, list({{'title': title, 'summary': summary, 'quals': quals}})[1:3] AS text_sample
    FROM (
      SELECT {series} AS series, positionTitle AS title,
             substr(COALESCE(json_extract_string({D},'$.UserArea.Details.JobSummary'),''), 1, 700) AS summary,
             substr(COALESCE(json_extract_string({D},'$.QualificationSummary'),''), 1, 900) AS quals,
             row_number() OVER (PARTITION BY {series}
                                ORDER BY length(COALESCE(json_extract_string({D},'$.QualificationSummary'),'')) DESC) AS rk
      FROM read_parquet([{urls}]), json_each(JobCategories) AS s
      WHERE {reach}
    ) WHERE rk <= 3 GROUP BY series""").df()
    text["text_sample"] = text.text_sample.map(
        lambda v: json.dumps([dict(x) for x in (v if v is not None and not isinstance(v, float) else [])]))
    emit(text, "series_text", "series")
    print(f"  E. posting text: {len(text):,} series sampled")

    # --- D. the actual live postings, so results can link to real jobs ----
    # /job/{usajobsControlNumber} is the stable public URL for an announcement.
    live = con.execute(f"""
    -- every open posting, not the first five: a result should be able to show
    -- the whole list of what you could apply to, and 4,165 rows across 308
    -- series is about 366 KB
    SELECT series, list({{'id': id, 'title': title, 'closes': closes}} ORDER BY closes) AS live_jobs
    FROM (
      SELECT {series} AS series, usajobsControlNumber AS id, positionTitle AS title,
             strftime(CAST(positionCloseDate AS TIMESTAMP), '%Y-%m-%d') AS closes
      FROM read_parquet([{urls}]), json_each(JobCategories) AS s
      WHERE {reach} AND CAST(positionCloseDate AS TIMESTAMP) > now()
    ) GROUP BY series""").df()
    con.close()

    def dedupe_titles(g, keep=5):
        """Collapse the same job advertised under many spellings.

        Agencies post one occupation as "Computer Scientist", "COMPUTER
        SCIENTIST", "INTERDISCIPLINARY ENGINEER/SCIENTIST", "SCIENTIST" and
        "INTERDISCIPLINARY". Case-fold first, then drop any title that contains
        or is contained by one already kept, keeping the most frequently posted
        wording as the representative of its family.
        """
        agg = {}
        for t, c in zip(g.title, g.c):
            k = " ".join(str(t).split()).upper()
            # agencies post the same job as "(TITLE 32)" and "(T32)"
            k = re.sub(r"\(?\bT32\b\)?", "(TITLE 32)", k)
            k = re.sub(r"\s+", " ", k).strip()
            if k:
                agg[k] = agg.get(k, 0) + int(c)
        out = []
        for k, _ in sorted(agg.items(), key=lambda kv: -kv[1]):
            if any(k in kept or kept in k for kept in out):
                continue
            out.append(k)
            if len(out) == keep:
                break
        # Capitalise each alphabetic run, so "(SYSADMIN)" does not become
        # "(sysadmin)" — str.capitalize() lowercases everything after the paren.
        # A short-word rule is not enough: it leaves "AND"/"NEW" shouting, so
        # real acronyms are listed instead.
        ACRONYMS = {"IT", "HR", "EEO", "VA", "FBI", "CBP", "TSA", "DOD", "GS", "WG",
                    "EMT", "CDL", "RN", "LPN", "IRS", "SSA", "ATC", "NCO", "MRI", "CT"}

        def nice(t):
            return re.sub(r"[A-Za-z]+",
                          lambda m: (m.group(0) if m.group(0).upper() in ACRONYMS
                                     else m.group(0).capitalize()), t)
        return [nice(t) for t in out]

    titles = (titles.groupby("series")[["title", "c"]]
                    .apply(dedupe_titles, include_groups=False)
                    .rename("common_titles").reset_index())
    print(f"  C. titles: {len(titles):,} series (deduped)")
    print(f"  D. live postings: {len(live):,} series with something open now")

    cur = (counts.merge(quals, on="series", how="left")
                 .merge(titles, on="series", how="left")
                 .merge(live, on="series", how="left")
                 .merge(geo, on="series", how="left"))
    def _as_list(v):
        """DuckDB LIST columns come back as ndarray, None, or pandas NA."""
        if v is None or isinstance(v, float) or v is pd.NA:
            return []
        return list(v)

    cur["months_seen"] = cur.months_seen.map(lambda m: sorted(int(x) for x in _as_list(m)))
    cur["common_titles"] = cur.common_titles.map(lambda t: json.dumps(_as_list(t)))
    cur["openings_by_state"] = cur.openings_by_state.fillna("{}")
    cur["live_jobs"] = cur.live_jobs.map(
        lambda v: json.dumps([{"id": str(j["id"]), "title": j["title"], "closes": j["closes"]}
                              for j in _as_list(v)]))

    # --- merge forward so lumpy hiring is not forgotten --------------------
    prev_p = DATA / "openings.parquet"
    if prev_p.exists():
        prev = pd.read_parquet(prev_p)[["series", "first_seen_open", "last_seen_open",
                                        "months_active", "ever_reachable"]]
        cur = cur.merge(prev, on="series", how="outer", suffixes=("", "_prev"))
        for c in ("ann_total", "ann_outsider", "ann_gradpath", "ann_reachable",
                  "openings_reachable", "open_now", "reachable_open_now", "openings_open_now"):
            cur[c] = cur[c].fillna(0).astype(int)
        cur["first_seen_open"] = cur[["first_seen_open", "first_seen_open_prev"]].min(axis=1)
        cur["last_seen_open"] = cur[["last_seen_open", "last_seen_open_prev"]].max(axis=1)
        # A series can sit in prev with nothing in the current window at all;
        # the outer merge then leaves NaN on its current-side columns, and
        # `nan or []` is nan (NaN is truthy), which used to crash here.
        cur["months_active"] = [
            sorted(set(_as_list(a)) | set(json.loads(b) if isinstance(b, str) else _as_list(b)))
            for a, b in zip(cur.months_seen, cur.months_active)]
        cur["ever_reachable"] = (cur.ever_reachable.fillna(False).astype(bool)
                                 | (cur.ann_reachable > 0))
        cur = cur.drop(columns=[c for c in cur.columns if c.endswith("_prev")])
    else:
        cur["months_active"] = cur.months_seen
        cur["ever_reachable"] = cur.ann_reachable > 0

    cur = cur.drop(columns=["months_seen"])
    gap = (pd.Timestamp.utcnow().tz_localize(None) - pd.to_datetime(cur.last_seen_open)).dt.days / 30.44
    cur["months_since_reachable"] = gap.round(1)
    cur["status"] = [
        "open_now" if r.reachable_open_now > 0
        else "never_reachable" if not r.ever_reachable
        else "unknown" if pd.isna(g)
        else "recently_active" if g <= DORMANT_AFTER_MONTHS
        else "dormant"
        for r, g in zip(cur.itertuples(), gap)]
    cur["months_active"] = cur.months_active.map(json.dumps)
    emit(cur, "openings", "series")
    return cur
