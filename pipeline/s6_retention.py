"""Stage 6 — do people stay in these jobs?

Career-matching tools are never evaluated on outcomes, because outcomes are not
usually available. Here they partly are: OPM publishes separations with a
separation_category (QUIT vs retirement vs termination) and length_of_service.

So per occupation we can compute the share of departures that are people
quitting early — leaving voluntarily inside two years. That is the closest thing
available to "the match did not work", and it is exactly the fine print a
22-year-old should see next to "9,000 hires a year".

Deliberate limits, stated because they matter for reading the number:
  * This is a share of SEPARATIONS, not a hazard rate on a hiring cohort. The
    denominator is people leaving, not people hired. A growing occupation and a
    shrinking one with identical quit behaviour will not look identical.
  * Retirements are excluded from the numerator but not the denominator, so
    occupations with an old workforce show a lower early-quit share for reasons
    that have nothing to do with new hires. `early_quit_share_of_quits` is
    reported alongside to control for that.
  * Terminations (expired appointment) are separated out: for seasonal work they
    are the normal end of a job, not a bad match.
"""
import json
import urllib.request

import duckdb
import pandas as pd

from .common import emit, pct
from .config import DATA, HF_BASE, HIRE_MONTHS

TREE = ("https://huggingface.co/api/datasets/impactproject/opm-ehri-data/"
        "tree/main/separations?limit=1000")
EARLY_YEARS = 2.0


def _separation_files():
    with urllib.request.urlopen(TREE, timeout=120) as r:
        entries = json.load(r)
    best = {}
    for e in entries:
        k = e.get("path", "")
        if not k.startswith("separations/separations_") or not k.endswith(".parquet"):
            continue
        month = k.split("_")[1]
        ver = int(k.split("_v")[1].split(".")[0])
        if month < HIRE_MONTHS[0] or (HIRE_MONTHS[1] and month > HIRE_MONTHS[1]):
            continue
        if best.get(month, (0,))[0] < ver:
            best[month] = (ver, k)
    if not best:
        raise RuntimeError(f"no separation files in window {HIRE_MONTHS}")
    return [best[m][1] for m in sorted(best)]


def run():
    print("stage 6: retention (OPM/EHRI separations)")
    files = _separation_files()
    print(f"  {len(files)} monthly files, {files[0].split('/')[-1]} … {files[-1].split('/')[-1]}")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET enable_progress_bar=false; SET preserve_insertion_order=false;")
    con.execute("SET threads=4; SET memory_limit='2GB'; SET max_temp_directory_size='1GiB';")
    con.execute(f"SET temp_directory='{DATA}/.duckdb_tmp';")
    urls = ",".join(f"'{HF_BASE}{f}'" for f in files)

    QUIT = "separation_category = 'QUIT'"
    RETIRE = "separation_category LIKE 'RETIREMENT%'"
    TERM = "separation_category LIKE 'TERMINATION%'"
    EARLY = f"TRY_CAST(length_of_service_years AS DOUBLE) < {EARLY_YEARS}"

    con2 = con
    df = con.execute(f"""
    SELECT occupational_series_code AS series,
      sum(n)                                   AS separations_total,
      sum(n) FILTER ({QUIT})                   AS quits,
      sum(n) FILTER ({QUIT} AND {EARLY})       AS early_quits,
      sum(n) FILTER ({RETIRE})                 AS retirements,
      sum(n) FILTER ({TERM})                   AS terminations,
      sum(n) FILTER ({TERM} AND {EARLY})       AS early_terminations,
      median(TRY_CAST(length_of_service_years AS DOUBLE)) FILTER ({QUIT}) AS median_years_at_quit
    FROM (SELECT *, TRY_CAST(count AS BIGINT) AS n FROM read_parquet([{urls}]))
    GROUP BY 1""").df()

    # A handful of separation rows carry no occupational series code.
    dropped = df.series.isna().sum()
    if dropped:
        print(f"  dropped {dropped} row(s) with no series code")
        df = df[df.series.notna()].copy()

    for c in df.columns:
        if c not in ("series", "median_years_at_quit"):
            df[c] = df[c].fillna(0).astype(int)

    df["early_quit_share_of_exits"] = pct(df.early_quits, df.separations_total)
    df["early_quit_share_of_quits"] = pct(df.early_quits, df.quits)
    df["retirement_share"] = pct(df.retirements, df.separations_total)
    df["term_share"] = pct(df.terminations, df.separations_total)
    print(f"  {len(df):,} series, {df.separations_total.sum():,} separations, "
          f"{df.early_quits.sum():,} early quits")
    emit(df, "retention", "series")

    yr = con2.execute(f"""
    SELECT occupational_series_code AS series,
           substr(personnel_action_effective_date_yyyymm, 1, 4) AS year,
           sum(n) FILTER ({QUIT} AND {EARLY}) AS early_quits,
           sum(n) AS separations
    FROM (SELECT *, TRY_CAST(count AS BIGINT) AS n FROM read_parquet([{urls}]))
    WHERE occupational_series_code IS NOT NULL
    GROUP BY 1, 2""").df()
    for c in ("early_quits", "separations"):
        yr[c] = yr[c].fillna(0).astype(int)
    emit(yr, "retention_by_year", "series")
    con2.close()
    return df
