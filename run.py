#!/usr/bin/env python3
"""Run the pipeline end to end and report what changed.

    python run.py              # fetch and rebuild, then diff against the previous run
    python run.py --full       # the above, then rebuild the instrument, then fold it in
    python run.py --stages 2,4 # re-run only some stages (others keep prior output)
    python run.py --diff-only  # no fetching; just re-diff what is on disk
    python run.py --stages 5   # rebuild the instrument (LLM; costs money, cached)

Stage 4 appears twice in a full run, and the repetition is the point. Stage 5
rates occupations from series_facts, which stage 4 builds; stage 4 writes the
site payload from the instrument, which stage 5 builds. Neither can go first, so
the data pass runs, the instrument is rebuilt against it, and the payload is
written again from the result.

Outputs land in data/. data/CHANGES.md is the human-readable diff.
"""
import argparse
import sys
import traceback

from pipeline import (s1_quiz, s2_openings, s3_hiring, s4_build, s5_instrument,
                      s7_standards)
from pipeline.common import diff_table, snapshot, write_changelog

# Stage 5 is not in the default set: it makes LLM calls, and it is the only
# stage that both reads and writes the question set. Responses are cached on
# disk, so re-running it after the first time is free and deterministic.
STAGES = {1: ("quiz", s1_quiz.run), 2: ("openings", s2_openings.run),
          3: ("hires", s3_hiring.run), 4: ("build", s4_build.run),
          5: ("instrument", s5_instrument.run), 7: ("standards", s7_standards.run)}
DEFAULT_STAGES = "1,2,3,7,4"   # 7 before 4: the fact table folds it in
FULL_STAGES = "1,2,3,7,4,5,4"  # see the module docstring for why 4 runs twice

# (table, key, columns whose movement is worth calling out)
WATCH = [
    ("questions", "question_id", ["question_text"]),
    ("series_profiles", "series", ["series_name", "profile"]),
    ("openings", "series", ["ann_reachable", "openings_reachable",
                            "reachable_open_now", "status",
                            "pct_degree_required", "pct_education_substitutable"]),
    ("hires", "series", ["hires_entry_perm", "hires_new"]),
    ("series_facts", "series", ["flag_count", "status", "hires_entry_perm",
                                "reachable_open_now", "pct_degree_required"]),
    ("opm_standards", "series", ["opm_degree_required"]),
    ("hires_by_state", ("series", "state"), ["entry_hires"]),
    ("hires_by_month", ("series", "month"), ["entry_hires", "new_hires"]),
    ("generated_questions", "question_id", ["text", "axis", "hiring_weighted_var"]),
    ("family_questions", "question_id", ["text"]),
    ("mixed_questions", "question_id", ["text", "origin"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default=DEFAULT_STAGES,
                    help="comma-separated stage numbers; 5 = LLM question generation")
    ap.add_argument("--full", action="store_true",
                    help="data, then the instrument, then the payload (makes LLM calls)")
    ap.add_argument("--diff-only", action="store_true")
    a = ap.parse_args()

    meta = {}
    failed = []
    if not a.diff_only:
        n = snapshot()
        meta["snapshotted"] = f"{n} previous tables"
        stages = FULL_STAGES if a.full else a.stages
        want = [int(s) for s in stages.split(",") if s.strip()]
        for i in want:   # order matters: 6 and 7 feed 4
            name, fn = STAGES[i]
            try:
                fn()
                key = f"stage {i} ({name})"
                meta[key if key not in meta else f"{key}, again"] = "ok"
            except Exception as e:
                meta[f"stage {i} ({name})"] = f"FAILED — {type(e).__name__}: {e}"
                failed.append(f"{i} ({name})")
                traceback.print_exc()
                print(f"  !! stage {i} failed; keeping previous data/{name} output")

    sections = []
    if failed:
        # A failed stage keeps its previous output, so downstream tables still
        # build and the diff still reads "no changes". That is exactly how a
        # silent failure hides. Say so at the top, and exit non-zero.
        sections.append(f"> **STAGES FAILED: {', '.join(failed)}** — "
                        f"their tables below are STALE, not unchanged.\n")
    for table, key, cols in WATCH:
        sections += diff_table(table, key, cols)
    write_changelog(sections, meta)
    if failed:
        print(f"\n!! {len(failed)} stage(s) failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
