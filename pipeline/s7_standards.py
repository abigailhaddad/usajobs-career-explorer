"""Stage 7 — what OPM's qualification standards actually require.

The posting regex only sees what an announcement chooses to restate, and most do
not restate the Individual Occupational Requirement. Computer science (1550) has
an absolute degree requirement — "Basic Requirements: Bachelor's degree in
computer science…" — and the posting text scored it at 5%. Telling someone a
degree is optional there is simply wrong.

These are OPM's own General Schedule Qualification Standards, one page per
series, scraped into the opm-educ-req repo.

Read the negative carefully: `False` means "no GS standard on file says a degree
is required", not "no degree needed". General attorney comes back False because
attorneys are excepted service and OPM publishes no GS standard for them, yet
every federal attorney job needs a JD. Only the positive is authoritative.
"""
import glob
import html
import os
import re

import pandas as pd

from .common import emit
from .config import STANDARDS_CACHE

DEGREE = re.compile(r"basic requirements:?\s*(?:[^.]{0,120})?\b(degree|bachelor)", re.I)
ALT = re.compile(r"experience,?\s+education,?\s+and\s+training|equivalent combination", re.I)


def _text(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    s = re.sub(r"<script.*?</script>|<style.*?</style>|<nav.*?</nav>"
               r"|<header.*?</header>|<footer.*?</footer>", "", s, flags=re.S | re.I)
    m = re.search(r"<main[^>]*>(.*?)</main>", s, re.S | re.I)
    if m:
        s = m.group(1)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def run():
    print("stage 7: OPM qualification standards")
    cache = STANDARDS_CACHE.expanduser()
    if not cache.exists():
        print(f"  !! {cache} not found — skipping; degree flags stay posting-derived")
        return None
    rows = []
    for f in sorted(set(glob.glob(str(cache / "*.html")))):
        m = re.search(r"(\d{4})\.html$", os.path.basename(f))
        if not m:
            continue
        t = _text(f)
        rows.append({"series": m.group(1),
                     "opm_degree_required": bool(DEGREE.search(t)),
                     "opm_experience_alt": bool(ALT.search(t)),
                     "standard_chars": len(t)})
    df = pd.DataFrame(rows).drop_duplicates("series")
    print(f"  {len(df)} series with a published standard; "
          f"{int(df.opm_degree_required.sum())} require a degree outright")
    emit(df, "opm_standards", "series")
    return df
