"""Qualification patterns, evaluated as lowercase regex inside DuckDB.

Deliberately regex and not a model: the pipeline is re-run and diffed, so a
change between runs should mean the postings changed, not that a model sampled
differently.

Each entry is {"match": ..., "unless": ...}. DuckDB uses RE2, which has no
lookbehind, so negation is expressed as a second pattern rather than inline.

This matters more than it sounds. Federal postings routinely state requirements
in the negative — "this position does not have a positive education requirement",
"no substitution of education for experience is permitted" — so a naive pattern
scores the exact opposite of the truth. Measured before the fix:
  degree_required          5 of 6 hits were false, 4 of them outright negations
  education_substitutable  3 of 28 hits were negated statements
Re-check this on a fresh sample before trusting it on a new posting vintage.
"""

PATTERNS = {
    # A degree is required outright, with no experience-only path.
    # The bare phrase "positive education requirement" is NOT enough: it appears
    # far more often inside "there is no positive education requirement for this
    # position" and inside generic transcript instructions.
    "degree_required": {
        "match": (r"this\s+position\s+has\s+a\s+positive\s+education\s+requirement"
                  r"|(must|shall)\s+(possess|have)\s+(a\s+)?(bachelor|master|doctor)"
                  r"|(bachelor'?s?|master'?s?|doctoral)\s+degree\s+is\s+required"
                  r"|degree\s+is\s+required"
                  r"|no\s+substitution\s+of\s+experience\s+for\s+education"),
        "unless": (r"(no|not\s+have\s+a|does\s+not\s+have\s+a)\s+positive\s+education"
                   r"|positions?\s+(with|requiring)\s+positive\s+education"),
    },
    # Education offered as one qualifying route alongside experience.
    "education_substitutable": {
        "match": (r"substitut\w*\s+(of\s+)?education\s+for\s+experience"
                  r"|education\s+may\s+be\s+substituted"
                  r"|combination\s+of\s+education\s+and\s+experience"),
        "unless": (r"no\s+substitution\s+of\s+education"
                   r"|education\s+(may\s+not|cannot|can\s+not)\s+be\s+substituted"),
    },
    "specialized_experience": {"match": r"specialized\s+experience"},
    "age_limit": {
        "match": (r"maximum\s+entry\s+age|prior\s+to\s+(your\s+)?37th\s+birthday"
                  r"|not\s+(have\s+)?reached\s+(your\s+)?37th\s+birthday"),
    },
    "license_or_cert": {
        "match": r"(licensure|licensed|certification|board[- ]certified|registered\s+nurse)",
    },
    "clearance": {"match": r"security\s+clearance|top\s+secret"},
}


def sql_expr(name: str, col: str = "txt") -> str:
    """SQL boolean for one pattern, honouring its `unless` clause."""
    spec = PATTERNS[name]
    q = lambda p: "'" + p.replace("'", "''") + "'"          # noqa: E731
    expr = f"regexp_matches({col}, {q(spec['match'])})"
    if spec.get("unless"):
        expr += f" AND NOT regexp_matches({col}, {q(spec['unless'])})"
    return expr
