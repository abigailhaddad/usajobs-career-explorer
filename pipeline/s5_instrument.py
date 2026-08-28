"""Stage 5 — build the question set the site ships, end to end.

The instrument used to be assembled by running four scripts by hand in an order
written down nowhere, which is how the site ended up shipping a question set
whose recipe was not in the repo. This is that recipe.

    families     which occupations are realistic alternatives to each other
    narrow       items written to split particular occupations apart
    broad        one item per family, so every kind of work has something a
                 person can react to
    combine      choose the split, then re-rate the chosen items together
    audit        judge the result on collapses, not on how many ties there are

Ordering is a real dependency chain, not a preference: families need the posting
text from stage 2, broad items need the families, and the combine step needs
both halves rated before it can choose between them.

    python run.py --stages 5
"""
from . import broad_items, combine, families, s5_questions
from .config import DATA


def _check_order():
    """series_facts is what every occupation blurb is built from.

    Stage 2 rewrites the posting text and the common titles that go into those
    blurbs, but stage 4 is what folds them into series_facts. Running stage 5
    against a stale series_facts produces ratings keyed to titles the site no
    longer shows: it cost 86 of 302 occupations once, and the mismatch is
    invisible in the output because every stage still reports success.
    """
    facts = DATA / "series_facts.parquet"
    if not facts.exists():
        raise SystemExit("data/series_facts.parquet is missing — run stage 4 first")
    stale = [p.name for p in (DATA / "openings.parquet", DATA / "hires.parquet")
             if p.exists() and p.stat().st_mtime > facts.stat().st_mtime]
    if stale:
        raise SystemExit(
            f"series_facts.parquet is older than {', '.join(stale)}.\n"
            f"Run `python run.py --stages 4` first: stage 5 rates occupations from "
            f"series_facts, so a stale one silently rates the wrong text.")


def run(n_final=25, n_narrow=None, limit=0):
    _check_order()
    print("stage 5a: work families")
    families.run(limit=limit)

    print("\nstage 5b: narrow items")
    s5_questions.run()

    print("\nstage 5c: broad items, one per family")
    broad_items.run()

    print("\nstage 5d: combine, re-rate, audit")
    combine.run(n_final=n_final, n_narrow=n_narrow)
