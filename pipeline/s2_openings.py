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
from .config import (UA, CURRENT_JOBS_YEARS, POSTINGS_WINDOW_MONTHS, DATA, ENTRY_MAX_GRADE, GRAD_PATHS, GS_LIKE,
                     PUBLIC_PATHS, R2_BASE, TRADE_PLANS)

DORMANT_AFTER_MONTHS = 18
D = "MatchedObjectDescriptor"


def _lst(vals):
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in vals)


# How many announcements to pull per series before choosing which to keep, and
# how many survive. Ranked by length first: a longer duties block says more.
#
# Keeping 3 was leaving evidence unused: the median series has 7 reachable
# announcements and the big hirers have hundreds, so three postings described
# occupations the model has to separate on the strength of the description.
# The candidate pool is wide because near-duplicates are now discarded rather
# than filling a slot, so the pool has to carry spares.
TEXT_CANDIDATES = 20
TEXT_KEEP = 5


def _words(s):
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def _overlap(a, b):
    """Jaccard word overlap. 1.0 means the two postings say the same thing."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pick_varied(g, keep=TEXT_KEEP):
    """Keep the longest posting, then the ones least like what is already kept.

    Ranking by length alone returned three copies of the same VA announcement.
    Deduping by hiring agency is not enough either: two agencies often run the
    same boilerplate. So compare the text itself and take the spread.
    """
    rows = g.to_dict("records")
    bags = [_words(r["duties"]) for r in rows]

    # Drop near-identical postings first. Returning early when there are only as
    # many candidates as slots shipped correctional officer with the same
    # Interior announcement twice, at an overlap of 1.000, because the early
    # return skipped the comparison entirely.
    unique, ubags = [], []
    for r, b in zip(rows, bags):
        if all(_overlap(b, ub) < 0.9 for ub in ubags):
            unique.append(r)
            ubags.append(b)
    rows, bags = unique, ubags
    if len(rows) <= keep:
        return rows
    chosen = [0]                                   # rows arrive longest-first
    while len(chosen) < keep:
        nxt = min((i for i in range(len(rows)) if i not in chosen),
                  key=lambda i: (max(_overlap(bags[i], bags[j]) for j in chosen), i))
        chosen.append(nxt)
    return [rows[i] for i in chosen]


def _connect():
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # Keep this stage inside a small envelope: the parquets are remote and wide,
    # and the machine this runs on has been tight on disk.
    con.execute("SET enable_progress_bar=false; SET preserve_insertion_order=false;")
    con.execute("SET threads=4; SET memory_limit='2GB'; SET max_temp_directory_size='6GiB';")
    con.execute(f"SET temp_directory='{DATA}/.duckdb_tmp';")
    return con


def _published_years():
    """CURRENT_JOBS_YEARS whose current_jobs file exists on R2. The newest year's
    file appears with the first posting of that year, so for a few days each
    January it may not; that year is skipped, out loud, rather than failing the
    stage or quietly reading nothing."""
    import urllib.error
    import urllib.request
    years = []
    for y in CURRENT_JOBS_YEARS:
        # R2's public endpoint 403s Python's default user agent.
        req = urllib.request.Request(f"{R2_BASE}/current_jobs_{y}.parquet", method="HEAD",
                                     headers={"User-Agent": UA})
        try:
            urllib.request.urlopen(req, timeout=60)
            years.append(y)
        except urllib.error.HTTPError as e:
            if e.code != 404 or y != CURRENT_JOBS_YEARS[-1]:
                raise
            print(f"  current_jobs_{y}.parquet is not on R2 yet; reading {years}")
    return years


def run():
    print("stage 2: announcements (R2 current_jobs)")
    urls = _lst(f"{R2_BASE}/current_jobs_{y}.parquet" for y in _published_years())
    con = _connect()
    # One row per control number: its latest listing. A reopened announcement
    # keeps its control number and lands in the file for the year it reopened,
    # while the old year's file still holds the earlier listing. On 2026-11-01
    # 285 control numbers sat in both current_jobs_2025 and _2026, every one
    # with a later open date in 2026, and 166 of them were counted twice inside
    # the posting window. The superseded rows are found on narrow columns and
    # dropped with a plain filter: a join against the full key list made DuckDB
    # buffer the wide descriptor column and run out of its 2 GB.
    stale = con.execute(f"""
    SELECT filename, CAST(usajobsControlNumber AS VARCHAR) AS cn
    FROM read_parquet([{urls}], filename=true)
    QUALIFY row_number() OVER (PARTITION BY usajobsControlNumber
                               ORDER BY CAST(positionOpenDate AS TIMESTAMP) DESC, filename DESC) > 1
    """).df()
    drop = " OR ".join(
        f"(filename = '{f}' AND CAST(usajobsControlNumber AS VARCHAR) IN ({_lst(g.cn)}))"
        for f, g in stale.groupby("filename")) or "false"
    con.execute(f"CREATE TEMP VIEW jobs AS SELECT * FROM read_parquet([{urls}], filename=true) "
                f"WHERE NOT ({drop})")
    print(f"  {len(stale):,} superseded listings of reopened announcements dropped")

    public_path = (f"EXISTS (SELECT 1 FROM json_each(HiringPaths) h "
                f"WHERE json_extract_string(h.value,'$.hiringPath') IN ({_lst(PUBLIC_PATHS)}))")
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
    reach = f"({public_path}) AND appointmentType='Permanent' AND {entry_grade}"
    openings = "COALESCE(TRY_CAST(totalOpenings AS INT),1)"
    # The posting statistics cover the last POSTINGS_WINDOW_MONTHS months.
    # Applied to the tallies, qualification shares, titles and text sample --
    # not to anything "open now", and not to first/last seen, which are history.
    win = (f"CAST(positionOpenDate AS TIMESTAMP) >= "
           f"now() - INTERVAL {POSTINGS_WINDOW_MONTHS} MONTH")

    # --- A. counts (no descriptor column touched) --------------------------
    counts = con.execute(f"""
    SELECT {series} AS series,
      count(*) FILTER ({win})                         AS ann_total,
      count(*) FILTER ({win} AND {public_path})       AS ann_public,
      count(*) FILTER ({win} AND {gradpath})          AS ann_gradpath,
      count(*) FILTER ({win} AND {reach})             AS ann_reachable,
      COALESCE(sum({openings}) FILTER ({win} AND {reach}),0) AS openings_reachable,
      count(*) FILTER (CAST(positionCloseDate AS TIMESTAMP) > now())           AS open_now,
      count(*) FILTER (CAST(positionCloseDate AS TIMESTAMP) > now() AND {reach}) AS reachable_open_now,
      COALESCE(sum({openings}) FILTER (CAST(positionCloseDate AS TIMESTAMP) > now() AND {reach}),0)
                                                      AS openings_open_now,
      min(CAST(positionOpenDate AS TIMESTAMP)) FILTER ({reach}) AS first_seen_open,
      max(CAST(positionOpenDate AS TIMESTAMP)) FILTER ({reach}) AS last_seen_open,
      median({grade}) FILTER ({win} AND {reach})      AS typical_entry_grade,
      list(DISTINCT month(CAST(positionOpenDate AS TIMESTAMP))) FILTER ({reach}) AS months_seen
    FROM jobs, json_each(JobCategories) AS s
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
      FROM jobs, json_each(JobCategories) AS s
      WHERE {reach} AND {win}
    ) GROUP BY series""").df()
    print(f"  B. qualification text: {len(quals):,} series scanned")

    # --- C. what these jobs are actually called ----------------------------
    # Entry-level postings only. Drawing from every posting open to the public
    # listed senior titles ("Supervisory ...", "Chief ...") for occupations
    # someone would enter near the bottom, which is not what the card is for.
    titles = con.execute(f"""
    SELECT {series} AS series, positionTitle AS title, count(*) AS c
    FROM jobs, json_each(JobCategories) AS s
    WHERE {reach} AND {win}
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
      FROM jobs, json_each(JobCategories) AS s
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
    #
    # MajorDuties, not JobSummary. JobSummary is where agencies put recruitment
    # boilerplate: every VA practical nurse posting led with the student loan
    # repayment programme and said nothing about nursing, so the model was
    # rating the occupation on a benefits blurb.
    # MajorDuties is an array of strings. The [*] form unescapes each element;
    # casting the whole array to VARCHAR[] instead leaves JSON quotes embedded
    # in the text, which is what the model then read.
    duties_expr = (f"array_to_string(json_extract_string({D}, "
                   f"'$.UserArea.Details.MajorDuties[*]'), ' ')")
    # One candidate per hiring agency before ranking. Without this the pool is
    # whoever writes longest: 0620 has DoD, Bureau of Prisons and DOJ postings,
    # but VA runs 954 of its 1,186 announcements and filled every slot.
    cands = con.execute(f"""
    SELECT series, title, agency, duties FROM (
      SELECT *, row_number() OVER (PARTITION BY series ORDER BY n DESC) AS rk
      FROM (
        SELECT *, row_number() OVER (PARTITION BY series, agency ORDER BY n DESC) AS rk_agency
        FROM (
          SELECT {series} AS series, positionTitle AS title,
                 hiringAgencyName AS agency,
                 substr({duties_expr}, 1, 6000) AS duties,
                 length({duties_expr}) AS n
          FROM jobs, json_each(JobCategories) AS s
          WHERE {reach} AND {win} AND length({duties_expr}) >= 200
        )
      ) WHERE rk_agency = 1
    ) WHERE rk <= {TEXT_CANDIDATES}""").df()

    text = (cands.groupby("series")[["title", "agency", "duties"]]
                 .apply(pick_varied, include_groups=False)
                 .rename("text_sample").reset_index())
    text["text_sample"] = text.text_sample.map(json.dumps)
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
      FROM jobs, json_each(JobCategories) AS s
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
        for c in ("ann_total", "ann_public", "ann_gradpath", "ann_reachable",
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
