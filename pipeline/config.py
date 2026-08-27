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
CURRENT_JOBS_YEARS = (2025, 2026)

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

# Numerically-graded GS-like pay plans. WG/WL trades are handled separately:
# they have no GS grade, so a grade<=9 test would wrongly read as "no entry door".
GS_LIKE = ("GS", "GG", "GL", "GW", "FG", "IM", "ND", "DB")
TRADE_PLANS = ("WG", "WL")
ENTRY_MAX_GRADE = 9
YOUNG_BRACKETS = ("LESS THAN 20", "20-24", "25-29")
