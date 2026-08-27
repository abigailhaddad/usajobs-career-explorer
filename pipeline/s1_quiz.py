"""Stage 1 — scrape the Career Explorer quiz itself.

The 32 questions and the 302 series scoring vectors are both embedded in page
HTML as JS literals (`ceQuestionsArray`, `ceJobSeries`). Scoring is entirely
client-side: z-score the responses, Pearson-correlate against each series'
32-float profile, sort descending. Reimplemented in site/score.js.
"""
import http.cookiejar
import json
import re
import urllib.request

import pandas as pd

from .common import emit
from .config import CE_QUIZ_URL, CE_RESULTS_URL, CE_SAVE_URL, UA


# One opener with a cookie jar for the whole stage: the antiforgery token is
# bound to the session cookie, so the POST 400s without it.
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    return _OPENER.open(req, timeout=60).read().decode("utf-8", "replace")


def _js_literal(html: str, name: str):
    """Pull `name = [...]` (optionally Array.from(...)) out of inline JS."""
    i = html.find(name)
    if i < 0:
        raise RuntimeError(f"{name} not found in page")
    start = html.index("[", i)
    depth = 0
    for j in range(start, len(html)):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[start:j + 1])
    raise RuntimeError(f"unterminated {name}")


def run():
    print("stage 1: career explorer quiz")
    quiz = _get(CE_QUIZ_URL)
    questions = sorted(_js_literal(quiz, "ceQuestionsArray"), key=lambda q: q["sortOrder"])
    if len(questions) != 32:
        print(f"  !! expected 32 questions, got {len(questions)}")

    # The 302 profile vectors only appear on a results page, which needs a
    # submitted response set. Any valid set works; the vectors are the same.
    token = re.search(r'name="request-verification-token" content="([^"]+)"', quiz).group(1)
    payload = json.dumps({
        "currentQuestionIndex": 32, "resultsPageIndex": 0, "responseId": 0,
        "responses": [{"questionID": q["questionID"], "responseValue": 3} for q in questions],
    }).encode()
    raw = _get(CE_SAVE_URL, payload,
               {"Content-Type": "application/json", "RequestVerificationToken": token,
                "Referer": CE_QUIZ_URL})
    # response is <?xml version="1.0"?><long>924761</long> — take the element,
    # not the first digits on the line (that would match the "1" in "1.0").
    m = re.search(r"<long>(\d+)</long>", raw) or re.fullmatch(r"\s*(\d+)\s*", raw)
    if not m:
        raise RuntimeError(f"could not parse response id from {raw[:120]!r}")
    rid = m.group(1)
    series = _js_literal(_get(CE_RESULTS_URL.format(rid)), "ceJobSeries")
    print(f"  {len(questions)} questions, {len(series)} series (results id {rid})")

    qdf = pd.DataFrame([{"question_id": q["questionID"], "sort_order": q["sortOrder"],
                         "question_text": q["questionText"].strip()} for q in questions])
    sdf = pd.DataFrame([{
        "series": s["seriesCode"],
        "series_name": s["seriesName"],
        "ce_description": re.sub(r"<[^>]+>", " ", s["description"] or "").strip(),
        "related_titles": " | ".join(t for t in s.get("relatedJobTitles", []) if t),
        "profile": json.dumps(s["questionValues"]),
    } for s in series])
    bad = sdf[sdf.profile.map(lambda p: len(json.loads(p)) != 32)]
    if len(bad):
        print(f"  !! {len(bad)} series with a profile vector that is not 32 long")

    emit(qdf, "questions", "question_id")
    emit(sdf, "series_profiles", "series")
    return sdf
