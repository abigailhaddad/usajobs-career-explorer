#!/usr/bin/env python3
"""Weekly data refresh: rebuild only what changed upstream, and say what changed.

Run by .github/workflows/refresh.yml. Locally: python refresh.py (it writes the
same files the workflow commits; nothing is pushed or deployed).

Until 2026-11-01 nothing refreshed the data at all. The page promised "what's
open now" from a build dated 2026-08-28: every number was two months old and
nothing on the page said so.

Each source has a fingerprint, kept in data/sources.json from the last refresh:

  openings   R2 current_jobs_{year}.parquet ETags      -> stage 2
  hires      the HuggingFace dataset's head commit      -> stage 3
  standards  github.com/abigailhaddad/opm-educ-req main -> stage 7 (NOT run here)
  quiz       stage 1 always runs (one page, seconds); its tables are compared
             with the committed ones afterwards

Stage 4 (the site payload) runs when any data stage ran. Two things are flagged
for a person rather than done here:
  * the quiz's questions or occupations changed -> the instrument needs
    `python run.py --full`, which makes LLM calls;
  * opm-educ-req moved -> stage 7 needs the 167 MB standards cache, which lives
    only in a local checkout of that repo, not in git.

Writes refresh_result.json: whether the published data changed (ignoring the
build date), and the flags.
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pandas as pd

from pipeline.config import CURRENT_JOBS_YEARS, DATA, R2_BASE, SITE, UA

SOURCES = DATA / "sources.json"
RESULT = Path("refresh_result.json")
HF_API = "https://huggingface.co/api/datasets/impactproject/opm-ehri-data"
STANDARDS_API = "https://api.github.com/repos/abigailhaddad/opm-educ-req/commits/main"


def _get(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60)


def fingerprints() -> dict:
    """Current fingerprint of each source. A source that can't be reached is
    recorded as None, which never equals a stored value, so its stage runs and
    fails loudly rather than being skipped as "unchanged"."""
    fp = {"openings": {}, "hires": None, "standards": None}
    for y in CURRENT_JOBS_YEARS:
        try:
            r = _get(f"{R2_BASE}/current_jobs_{y}.parquet", "HEAD")
            fp["openings"][str(y)] = r.headers.get("ETag") or r.headers.get("Last-Modified")
        except Exception as e:
            fp["openings"][str(y)] = None
            print(f"  could not fingerprint current_jobs_{y}: {e}")
    try:
        fp["hires"] = json.load(_get(HF_API)).get("sha")
    except Exception as e:
        print(f"  could not fingerprint the hires dataset: {e}")
    try:
        fp["standards"] = json.load(_get(STANDARDS_API))["sha"]
    except Exception as e:
        print(f"  could not fingerprint opm-educ-req: {e}")
    return fp


def committed(path: Path) -> bytes | None:
    """The file as of HEAD, or None if it isn't committed."""
    r = subprocess.run(["git", "show", f"HEAD:{path.as_posix()}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def table_changed(name: str) -> bool:
    import io
    old = committed(Path("data") / f"{name}.parquet")
    if old is None:
        return True
    a = pd.read_parquet(io.BytesIO(old))
    b = pd.read_parquet(DATA / f"{name}.parquet")
    return not a.equals(b)


def payload_changed() -> bool:
    """site/data.json differs from HEAD in anything but its build date."""
    old = committed(Path("site/data.json"))
    if old is None:
        return True
    a, b = json.loads(old), json.loads((SITE / "data.json").read_text())
    a.pop("built", None), b.pop("built", None)
    return a != b


def main() -> int:
    # --force rebuilds every data stage whatever the fingerprints say (the
    # workflow's manual-dispatch checkbox).
    force = "--force" in sys.argv[1:]
    stored = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    now = fingerprints()
    stages = [1]
    if force or now["openings"] != stored.get("openings") or None in now["openings"].values():
        stages.append(2)
    if force or now["hires"] != stored.get("hires") or now["hires"] is None:
        stages.append(3)
    print(f"sources: {json.dumps(now)}")
    print(f"previous: {json.dumps(stored)}")

    # Stage 1 always; stage 4 only if something it reads may have moved.
    run = stages + [4] if len(stages) > 1 else stages
    print(f"running stages {run}")
    rc = subprocess.run([sys.executable, "run.py", "--stages", ",".join(map(str, run))]).returncode
    if rc != 0:
        print("run.py failed; see data/CHANGES.md. Nothing recorded.")
        return rc

    quiz_changed = table_changed("questions") or table_changed("series_profiles")
    if quiz_changed and 4 not in run:
        # The quiz moved on its own: fold it into the payload too.
        rc = subprocess.run([sys.executable, "run.py", "--stages", "4"]).returncode
        if rc != 0:
            return rc

    flags = []
    if quiz_changed:
        flags.append("USAJOBS Career Explorer's questions or occupations changed: the "
                     "25-question instrument was built for the old set. Run "
                     "`python run.py --full` locally (LLM calls) and review data/CHANGES.md.")
    if stored.get("standards") and now["standards"] and now["standards"] != stored["standards"]:
        flags.append("opm-educ-req has new commits: the degree-requirement table "
                     "(stage 7) may be out of date. Run `python run.py --stages 7,4` "
                     "locally, with that repo checked out beside this one.")

    result = {"stages_run": run, "payload_changed": payload_changed(), "flags": flags}
    RESULT.write_text(json.dumps(result, indent=2))
    # The standards fingerprint is only advanced once someone has acted on it
    # (by running stage 7 and updating data/sources.json in the same commit);
    # otherwise the flag would fire once and then go quiet with stage 7 stale.
    if stored.get("standards"):
        now["standards"] = stored["standards"]
    SOURCES.write_text(json.dumps(now, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
