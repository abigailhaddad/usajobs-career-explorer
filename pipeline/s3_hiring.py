"""Stage 3 — actual hires per series, from OPM/EHRI accessions on HuggingFace.

Announcements say what was advertised; accessions say who actually got hired.
They disagree often enough (log-log r = 0.52) that the tool needs both.

Scope choices worth knowing:
  * new hires only — transfers-in and mass transfers are excluded, they are not
    a route into government for someone outside it;
  * permanent = OPM tenure groups 1-2 (career / career-conditional);
  * entry door = grade 01-09 on a GS-like plan, OR any WG/WL trades grade. Trades
    have no GS grade at all, so a grade<=9 test alone reads them as "no entry".
"""
import json
import urllib.request

import duckdb
import pandas as pd

from .common import emit
from .config import (DATA, ENTRY_MAX_GRADE, GS_LIKE, HF_BASE, HIRE_MONTHS,
                      TRADE_PLANS, YOUNG_BRACKETS)

# The dataset has no manifest on HF (that file lives in the pipeline repo), so
# the file list comes from the HF tree API. Only the months inside HIRE_MONTHS
# are ever fetched — there are 390+ files back to 2005 and we need 60.
TREE = ("https://huggingface.co/api/datasets/impactproject/opm-ehri-data/"
        "tree/main/accessions?limit=1000")


def _sql_list(vals):
    return ",".join(f"'{v}'" for v in vals)


def _accession_files():
    """Highest version of each monthly accessions file inside the window."""
    with urllib.request.urlopen(TREE, timeout=120) as r:
        entries = json.load(r)
    best = {}
    for e in entries:
        k = e.get("path", "")
        if not k.startswith("accessions/accessions_") or not k.endswith(".parquet"):
            continue
        month = k.split("_")[1]
        ver = int(k.split("_v")[1].split(".")[0])
        if month < HIRE_MONTHS[0] or (HIRE_MONTHS[1] and month > HIRE_MONTHS[1]):
            continue
        if best.get(month, (0,))[0] < ver:
            best[month] = (ver, k)
    if not best:
        raise RuntimeError(f"no accession files found in window {HIRE_MONTHS}")
    return [best[m][1] for m in sorted(best)]


def run():
    print("stage 3: hires (OPM/EHRI accessions)")
    files = _accession_files()
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET enable_progress_bar=false; SET preserve_insertion_order=false;")
    con.execute("SET threads=4; SET memory_limit='2GB'; SET max_temp_directory_size='1GiB';")
    con.execute(f"SET temp_directory='{DATA}/.duckdb_tmp';")
    print(f"  {len(files)} monthly files, {files[0].split('/')[-1]} … {files[-1].split('/')[-1]}")
    urls = _sql_list(HF_BASE + f for f in files)

    NEW = "accession_category LIKE 'NEW HIRE%'"
    PERM = "(tenure LIKE 'TENURE GROUP 1%' OR tenure LIKE 'TENURE GROUP 2%')"
    ENTRY = (f"((pay_plan_code IN ({_sql_list(GS_LIKE)}) AND TRY_CAST(grade AS INT) "
             f"BETWEEN 1 AND {ENTRY_MAX_GRADE}) OR pay_plan_code IN ({_sql_list(TRADE_PLANS)}))")
    YOUNG = f"age_bracket IN ({_sql_list(YOUNG_BRACKETS)})"

    df = con.execute(f"""
    SELECT occupational_series_code AS series,
      sum(n)                                              AS hires_total,
      sum(n) FILTER ({NEW})                               AS hires_new,
      sum(n) FILTER ({NEW} AND {PERM})                    AS hires_new_perm,
      sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY})        AS hires_entry_perm,
      sum(n) FILTER ({NEW} AND {ENTRY} AND NOT {PERM})    AS hires_entry_temp,
      sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY} AND {YOUNG}) AS hires_entry_perm_young,
      sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY} AND age_bracket
                     IN ('40-44','45-49','50-54','55-59','60-64','65 OR MORE')) AS hires_entry_perm_40plus,
      sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY} AND veteran_indicator='Y') AS hires_entry_perm_vet,
      sum(n) FILTER (pathways_group IS NOT NULL)          AS hires_pathways,
      sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY}
                     AND education_level_bracket <> 'NO DATA REPORTED') AS edu_reported,
      sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY} AND education_level_bracket IN
            ('BACHELORS DEGREE','MASTERS OR PROFESSIONAL DEGREE','DOCTORATE DEGREE'))
                                                          AS edu_bachelors_plus,
      sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY} AND education_level_bracket IN
            ('SOME COLLEGE OR ASSOCIATES DEGREE','BACHELORS DEGREE',
             'MASTERS OR PROFESSIONAL DEGREE','DOCTORATE DEGREE'))
                                                          AS edu_any_college,
      -- Same three counts over permanent new hires at ANY grade. Some
      -- occupations are barely entered below GS-11 — general attorney takes
      -- 20,451 new hires and 16 of them at entry grade — so the credential
      -- question has to be answered from the people they actually hire.
      sum(n) FILTER ({NEW} AND {PERM}
                     AND education_level_bracket <> 'NO DATA REPORTED') AS edu_reported_any,
      sum(n) FILTER ({NEW} AND {PERM} AND education_level_bracket IN
            ('BACHELORS DEGREE','MASTERS OR PROFESSIONAL DEGREE','DOCTORATE DEGREE'))
                                                          AS edu_bachelors_plus_any,
      sum(n) FILTER ({NEW} AND {PERM} AND education_level_bracket IN
            ('SOME COLLEGE OR ASSOCIATES DEGREE','BACHELORS DEGREE',
             'MASTERS OR PROFESSIONAL DEGREE','DOCTORATE DEGREE'))
                                                          AS edu_any_college_any,
      any_value(occupational_series)                      AS opm_series_name
    FROM (SELECT *, TRY_CAST(count AS BIGINT) AS n FROM read_parquet([{urls}]))
    GROUP BY 1""").df()
    for c in df.columns:
        if c not in ("series", "opm_series_name"):
            df[c] = df[c].fillna(0).astype(int)
    print(f"  {len(df):,} series, {df.hires_total.sum():,} accessions")
    emit(df, "hires", "series")

    # Hiring over time, monthly. A pooled multi-year total is a bad summary of a
    # market that moved. Monthly rather than yearly on purpose: the latest year
    # is partial (the data ends mid-year), and comparing a half year against
    # full-year peaks halves every ratio and invents a collapse.
    yr = con.execute(f"""
    SELECT occupational_series_code AS series,
           personnel_action_effective_date_yyyymm AS month,
           sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY}) AS entry_hires,
           sum(n) FILTER ({NEW}) AS new_hires
    FROM (SELECT *, TRY_CAST(count AS BIGINT) AS n FROM read_parquet([{urls}]))
    GROUP BY 1, 2""").df()
    for c in ("entry_hires", "new_hires"):
        yr[c] = yr[c].fillna(0).astype(int)
    yr = yr[yr.month.notna() & (yr.month >= HIRE_MONTHS[0])]
    print(f"  {yr.month.nunique()} months of hiring history "
          f"({yr.month.min()}-{yr.month.max()})")
    emit(yr, "hires_by_month", "series")

    # Where the entry-level hiring actually happens. Geography is a harder
    # constraint than interest for most people and was missing entirely.
    geo = con.execute(f"""
    SELECT occupational_series_code AS series,
           duty_station_state AS state,
           sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY}) AS entry_hires
    FROM (SELECT *, TRY_CAST(count AS BIGINT) AS n FROM read_parquet([{urls}]))
    WHERE duty_station_state IS NOT NULL
    GROUP BY 1, 2 HAVING sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY}) > 0""").df()
    geo["entry_hires"] = geo.entry_hires.astype(int)
    print(f"  {len(geo):,} series-state pairs with permanent entry hiring")
    emit(geo, "hires_by_state", "series")
    return df
