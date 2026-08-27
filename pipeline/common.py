"""Snapshot / diff helpers.

Every stage writes one parquet through `emit`. The runner snapshots the previous
outputs to data/.prev first, so after a run `diff_all` can say exactly what moved.
"""
import json
import re
import shutil
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import DATA, PREV


def pct(num, den, digits=1):
    """num/den as a percentage, NaN where den is 0.

    Uses np.nan rather than pd.NA on purpose: NAType has no __round__, so pd.NA
    poisons any later .round() call. This has bitten three separate stages.
    """
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    safe = np.where(den > 0, den, 1.0)
    return np.round(np.where(den > 0, 100.0 * num / safe, np.nan), digits)


def emit(df: pd.DataFrame, name: str, key: str | None = None) -> pd.DataFrame:
    DATA.mkdir(parents=True, exist_ok=True)
    if key:
        df = df.sort_values(key).reset_index(drop=True)
    df.to_parquet(DATA / f"{name}.parquet", index=False)
    print(f"  wrote data/{name}.parquet  ({len(df):,} rows, {len(df.columns)} cols)")
    return df


def snapshot():
    """Move current outputs aside so this run can be diffed against them."""
    if PREV.exists():
        shutil.rmtree(PREV)
    PREV.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in DATA.glob("*.parquet"):
        shutil.copy2(f, PREV / f.name)
        n += 1
    return n


def _fmt(v, sign=False):
    """Numbers with thousands separators; `sign` forces a leading + or -."""
    if isinstance(v, (int, float)):
        spec = "+" if sign else ""
        return f"{v:{spec},.1f}" if float(v) % 1 else f"{v:{spec},.0f}"
    return str(v)


def diff_table(name, key, watch: list[str]) -> list[str]:
    """Compare one table against its snapshot. Returns markdown lines.

    `key` may be a tuple of columns for tables keyed on more than one (e.g.
    series x year); they are joined into a single comparable key.
    """
    cur_p, prev_p = DATA / f"{name}.parquet", PREV / f"{name}.parquet"
    if not cur_p.exists():
        return [f"### {name}", "- not produced this run", ""]
    cur = pd.read_parquet(cur_p)
    if not prev_p.exists():
        return [f"### {name}", f"- **new table** — {len(cur):,} rows", ""]

    prev = pd.read_parquet(prev_p)
    # Keys must be comparable to be sorted; a stray null or mixed type in one
    # table should not take the whole changelog down.
    if isinstance(key, (tuple, list)):
        cols, key = list(key), " | ".join(key)
        for d in (cur, prev):
            d[key] = d[cols].astype("string").agg(" | ".join, axis=1)
    for d in (cur, prev):
        d[key] = d[key].astype("string")
    cur, prev = cur[cur[key].notna()], prev[prev[key].notna()]
    if cur[key].duplicated().any() or prev[key].duplicated().any():
        return [f"### {name}",
                f"- rows: {len(prev):,} → {len(cur):,}",
                f"- key `{key}` is not unique; per-row diff skipped", ""]
    out = [f"### {name}"]
    added = set(cur[key]) - set(prev[key])
    removed = set(prev[key]) - set(cur[key])
    out.append(f"- rows: {len(prev):,} → {len(cur):,}"
               + (f"  (+{len(added)} added, -{len(removed)} removed)" if added or removed else ""))
    new_cols = [c for c in cur.columns if c not in prev.columns]
    gone_cols = [c for c in prev.columns if c not in cur.columns]
    if new_cols:
        out.append(f"- **new columns**: {', '.join(new_cols)}")
    if gone_cols:
        out.append(f"- **columns removed**: {', '.join(gone_cols)}")
    quiet_upto = len(out)  # anything appended past here is a real change
    if added:
        out.append(f"  - added: {', '.join(sorted(map(str, added))[:12])}")
    if removed:
        out.append(f"  - removed: {', '.join(sorted(map(str, removed))[:12])}")

    both = sorted(set(cur[key]) & set(prev[key]))
    c = cur[cur[key].isin(both)].set_index(key).sort_index()
    p = prev[prev[key].isin(both)].set_index(key).sort_index()
    for col in watch:
        if col not in c.columns or col not in p.columns:
            continue
        # is_numeric_dtype says True for bool, and subtracting booleans raises.
        numeric = all(pd.api.types.is_numeric_dtype(d[col])
                      and not pd.api.types.is_bool_dtype(d[col]) for d in (c, p))
        if numeric:
            d = (c[col].fillna(0) - p[col].fillna(0))
            moved = d[d != 0]
            if len(moved) == 0:
                continue
            tot_c, tot_p = c[col].sum(), p[col].sum()
            out.append(f"- `{col}`: total {_fmt(tot_p)} → {_fmt(tot_c)} "
                       f"({_fmt(tot_c - tot_p, sign=True)}); {len(moved)} rows changed")
            top = moved.reindex(moved.abs().sort_values(ascending=False).index).head(5)
            for k, v in top.items():
                out.append(f"  - {k}: {_fmt(p.at[k, col])} → {_fmt(c.at[k, col])} ({_fmt(v, sign=True)})")
        else:
            ch = (c[col].astype(str) != p[col].astype(str))
            if ch.any():
                out.append(f"- `{col}`: {ch.sum()} rows changed value")
    if len(out) == quiet_upto and not (added or removed or new_cols or gone_cols):
        out.append("- no changes")
    out.append("")
    return out


def write_changelog(sections: list[str], meta: dict):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Pipeline run — {stamp}", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += ["", "## Changes since the previous run", ""] + sections
    (DATA / "CHANGES.md").write_text("\n".join(lines) + "\n")
    (DATA / "run_meta.json").write_text(json.dumps({"run_at": stamp, **meta}, indent=2, default=str))
    print("\n".join(lines))


# --- presentation ---------------------------------------------------------
# USAJOBS position titles arrive in a mix of cases: "Interdisciplinary" from one
# agency, "INTERDISCIPLINARY" from the next. Shouting on a results card reads as
# a bug, so all-caps titles get cased down. Titles that already carry lowercase
# are left exactly as posted.
_KEEP_UPPER = {
    "IT", "HR", "GS", "WG", "WL", "VA", "DOD", "DOJ", "DHS", "FBI", "DEA", "ATF",
    "ICE", "CBP", "TSA", "IRS", "EPA", "USDA", "NASA", "FAA", "OIG", "CID", "JAG",
    "EMT", "BLS", "ALS", "RN", "LPN", "LVN", "CNA", "MRI", "CT", "ICU", "ER",
    "PA", "NP", "MD", "DO", "PT", "OT", "PHS", "DHA", "SES", "STEM", "LEO", "EOD",
    "HVAC", "CNC", "NDT", "GIS", "QA", "QC", "EEO", "FOIA", "CIO", "ISSO", "SCI",
    "TS", "CDL", "ATC", "CONUS", "OCONUS", "US", "USA", "USAF", "USMC", "USCG",
    "USCIS", "AFSC", "K9", "ADP", "IED", "UAS", "POL", "AC",
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
}
_KEEP_LOWER = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of",
               "on", "or", "the", "to", "with"}


def titlecase(text: str) -> str:
    """Case down a SHOUTED job title, keeping acronyms and roman numerals."""
    if not text or any(c.islower() for c in text):
        return text

    def word(w, first):
        core = w.strip("().,/-&'")
        if not core:
            return w
        if core in _KEEP_UPPER:
            return w
        lowered = w.lower()
        if not first and lowered.strip("().,/-&'") in _KEEP_LOWER:
            return lowered
        # Recase each alphabetic run so "BLS/HAZMAT" and "MULTI-MEDIA" both work.
        return re.sub(r"[A-Za-z']+",
                      lambda m: (m.group(0).upper() if m.group(0).upper() in _KEEP_UPPER
                                 else m.group(0).capitalize()),
                      lowered)

    parts = text.split(" ")
    return " ".join(word(w, i == 0) for i, w in enumerate(parts))
