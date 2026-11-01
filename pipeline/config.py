"""Shared constants and paths."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PREV = DATA / ".prev"
SITE = ROOT / "site"

# --- sources -------------------------------------------------------------
# USAJOBS Career Explorer. Both the 32 questions and the 302 scoring vectors
# are embedded in the page HTML; there is no API.
CE_QUIZ_URL = "https://www.usajobs.gov/careerexplorer/quiz"
CE_SAVE_URL = "https://www.usajobs.gov/careerexplorer/saveresponses"
CE_RESULTS_URL = "https://www.usajobs.gov/careerexplorer/results/{}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Announcements: the usajobs_historical R2 bucket. current_jobs carries the full
# MatchedObjectDescriptor (JobSummary / QualificationSummary / Education);
# historical_jobs is metadata only, so it is deliberately not used here.
R2_BASE = "https://pub-317c58882ec04f329b63842c1eb65b0c.r2.dev/data"
# What the posting statistics describe: announcements opened in the last
# POSTINGS_WINDOW_MONTHS months (2026-11-01; was "since January 2025", a fixed
# start that kept growing the window and a fixed (2025, 2026) file list that
# would have stopped seeing new postings on 1 January 2027). "Open now" is not
# windowed -- an announcement still open is open however long ago it opened --
# and the first/last-seen history is carried forward across runs (s2_openings).
# The files read are every year the window touches; a year whose file isn't on
# R2 yet (the first days of January) is skipped by s2_openings, which says so.
POSTINGS_WINDOW_MONTHS = 12
_window_start = (__import__("datetime").date.today()
                 - __import__("datetime").timedelta(days=POSTINGS_WINDOW_MONTHS * 31))
CURRENT_JOBS_YEARS = tuple(range(_window_start.year, __import__("datetime").date.today().year + 1))

# Hires: OPM/EHRI accessions on HuggingFace (impactproject/opm-ehri-data).
HF_BASE = "https://huggingface.co/datasets/impactproject/opm-ehri-data/resolve/main/"
# Inclusive YYYYMM window. End is None = "whatever is published", so a re-run
# picks up new months automatically. A fixed end silently ages: this was pinned
# at 202512 while the dataset had run on to 202606, quietly discarding the six
# most recent months — the ones that matter most during a hiring freeze.
HIRE_MONTHS = ("202101", None)

# OPM's published GS Qualification Standards, one HTML page per series, already
# scraped by github.com/abigailhaddad/opm-educ-req. Authoritative for whether a
# series carries an Individual Occupational Requirement, which postings usually
# do not restate. Defaults to that repo checked out beside this one; override
# with OPM_STANDARDS_CACHE.
STANDARDS_CACHE = Path(os.environ.get(
    "OPM_STANDARDS_CACHE", ROOT.parent / "opm-educ-req" / "cache")).expanduser()

# --- definitions used across stages ---------------------------------------
# Hiring paths that do not require already being a federal employee. USAJOBS emits both a slug and a
# display-name encoding of the same path, so both are listed.
PUBLIC_PATHS = ("public", "The public", "student", "Students",
                  "graduates", "Recent graduates")
GRAD_PATHS = ("student", "Students", "graduates", "Recent graduates")

# Numerically-graded GS-like pay plans, where a low number really does mean
# junior. WG/WL trades are handled separately: they have no GS grade, so a
# grade<=9 test would wrongly read as "no entry door".
#
# ND, DB and IM were in this list and are not GS-like. They are demonstration
# projects with pay BANDS, where a handful of bands span the whole GS range and
# a low number means senior: ND-6 is a Distinguished Scientist. Counting them as
# entry level made every one of physics' and mathematics' entry hires a senior
# band scientist, 72% of computer science's, and 78% of computer engineering's.
# Same trap as the ES postings that read as grade 01, one list further along.
GS_LIKE = ("GS", "GG", "GL", "GW", "FG")
BANDED_PLANS = ("ND", "DB", "IM")   # excluded on purpose; see above
TRADE_PLANS = ("WG", "WL")
ENTRY_MAX_GRADE = 9
YOUNG_BRACKETS = ("LESS THAN 20", "20-24", "25-29")
