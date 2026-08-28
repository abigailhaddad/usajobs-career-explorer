#!/usr/bin/env python3
"""Build the browsable appendix: every LLM call behind the quiz.

Rating calls are reconstructed from the code and then verified: the prompt is
rebuilt, hashed the way pipeline/llm.py hashes it, and matched against the
response sitting at that key in data/.llm_cache. A row only claims to be a
matched pair if the key resolves, so nothing here is a paraphrase of a call.

Generation responses are shown too, but the cache is keyed by hash with no
stored prompt, and a generation prompt depends on which occupations were
batched together on that run. Those are labelled as unpaired.

The page is a DataTable using site/shared (the shared-ui filter bar, modals and
expandable rows), so filtering works the way it does in the other dashboards:
one "+ Add filter" button, a dialog per field, chips for what is active.

Written by stage 4, after site/data.json.
"""
import html
import json
from pathlib import Path

import pandas as pd
import yaml

from . import llm, s5_questions as s5
from .config import DATA, SITE

CACHE = DATA / ".llm_cache"


def esc(x):
    return html.escape(str(x if x is not None else ""))


def rating_calls(cfg, facts, questions, text_by_series):
    """Rebuild every rating prompt and pair it with its cached response."""
    mc = cfg["model"]
    smax = cfg["rating"]["scale_max"]
    system = cfg["rating"]["prompt"]
    schema = s5.RatingSet.model_json_schema()
    qlist = "\n".join(f"{i}. {t}" for i, t in enumerate(questions))
    out = []
    for r in facts.itertuples():
        prompt = (f"Occupation:\n{s5._occ_blurb(r, text_by_series)}\n\n"
                  f"Score every statement 0-{smax}. "
                  f"Return one entry per statement id.\n\n{qlist}")
        key = llm._key(mc["rate"], system, prompt, schema)
        path = CACHE / f"{key}.json"
        out.append({"row": r, "prompt": prompt, "key": key,
                    "resp": json.loads(path.read_text()) if path.exists() else None,
                    "sample": text_by_series.get(r.series, [])})
    return out


def generation_responses(limit=12):
    """Cached CandidateBatch payloads. Prompts are not reconstructable."""
    found = []
    for f in sorted(CACHE.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if (isinstance(d, dict) and d.get("questions")
                and isinstance(d["questions"], list)
                and isinstance(d["questions"][0], dict) and "axis" in d["questions"][0]):
            found.append((f.stem, d["questions"]))
        if len(found) >= limit:
            break
    return found


def family_labels(facts):
    """Short name per work family, from its two biggest hirers."""
    import numpy as np
    lab_path, idx_path = DATA / "llm_labels_12.npy", DATA / "llm_families_index.csv"
    if not (lab_path.exists() and idx_path.exists()):
        return {}
    lab = np.load(lab_path)
    idx = pd.read_csv(idx_path, dtype={"series": str})
    if list(idx.series) != list(facts.series):
        return {}
    names = {c: " / ".join(s.strip() for s in
                           facts[lab == c].nlargest(2, "hires_entry_perm").series_name)
             for c in sorted(set(lab))}
    return dict(zip(facts.series, (names[c] for c in lab)))


def gen_system(cfg):
    """The generation system prompt, built the way stage 5 builds it."""
    gen = cfg["generation"]
    axes = "\n".join(f"- {a}" for a in gen["axes"])
    return (f"{gen['system'].strip()}\n\n"
            f"Draw items from these axes:\n{axes}\n\n{gen['style']}\n\n{gen['avoid']}")


def run(out=None):
    out = Path(out) if out else SITE / "appendix.html"
    cfg = yaml.safe_load(s5.CONFIG.read_text())
    facts = pd.read_parquet(DATA / "series_facts.parquet").reset_index(drop=True)
    qs = pd.read_parquet(DATA / "mixed_questions.parquet")
    text_by_series = {r.series: json.loads(r.text_sample)
                      for r in pd.read_parquet(DATA / "series_text.parquet").itertuples()}
    questions = list(qs.text)
    origins = list(qs.origin) if "origin" in qs else [""] * len(questions)

    calls = rating_calls(cfg, facts, questions, text_by_series)
    matched = sum(1 for c in calls if c["resp"])
    fam_of = family_labels(facts)

    rows = []
    for c in calls:
        r, resp = c["row"], c["resp"]
        agencies = sorted({d.get("agency") for d in c["sample"] if d.get("agency")})
        scores = ({x["question_id"]: x["score"] for x in resp["ratings"]} if resp else {})
        rows.append({
            "name": r.series_name,
            "series": r.series,
            "hires": int(r.hires_per_year),
            "postings": len(c["sample"]),
            "agency": "; ".join(agencies),
            "family": fam_of.get(r.series, ""),
            "degree": r.degree_requirement,
            "paired": "matched" if resp else "no response",
            "key": c["key"],
            "prompt": c["prompt"],
            "sample": [{"title": d.get("title"), "agency": d.get("agency"),
                        "duties": d.get("duties") or d.get("summary") or ""}
                       for d in c["sample"]],
            "ratings": [{"i": i, "q": q, "s": scores.get(i)} for i, q in enumerate(questions)],
            "raw": json.dumps(resp, separators=(",", ":")) if resp else "",
        })

    gens = []
    for key, items in generation_responses():
        subj = []
        for it in items:
            for part in str(it.get("separates") or "").split(" vs "):
                nm = part.strip(" .;")
                if nm and nm not in subj:
                    subj.append(nm)
        gens.append({"key": key, "subject": subj, "items": items})

    qrows = "".join(
        f"<tr><td class='qid'>{i}</td><td>{esc(t)}</td><td class='origin'>{esc(o)}</td></tr>"
        for i, (t, o) in enumerate(zip(questions, origins)))

    page = (TEMPLATE
            .replace("__ROWS__", json.dumps(rows))
            .replace("__GENS__", json.dumps(gens))
            .replace("__QROWS__", qrows)
            .replace("__N__", f"{len(calls):,}")
            .replace("__MATCHED__", f"{matched:,}")
            .replace("__NQ__", str(len(questions)))
            .replace("__RATE_MODEL__", esc(cfg["model"]["rate"]))
            .replace("__GEN_MODEL__", esc(cfg["model"]["generate"]))
            .replace("__RATE_SYSTEM__", esc(cfg["rating"]["prompt"].strip()))
            .replace("__GEN_SYSTEM__", esc(gen_system(cfg))))
    out.write_text(page)
    print(f"  wrote {out.name}")
    print(f"    {len(calls)} occupations, {matched} matched to a cached response, "
          f"{len(gens)} generation batches, {out.stat().st_size/1024:.0f} KB")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Every LLM call behind the quiz</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css">
<link rel="stylesheet" href="shared/shared.css">
<style>
 :root { --sui-primary: #1a4480; --sui-accent: #1a4480; --sui-accent-light: #cfe0f5;
         --sui-accent-lighter: #eef3fa; --sui-bg-alt: #f7f8fa; }
 body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
        color: #1b1b1b; padding: 2rem 1.25rem 4rem; }
 .wrap { max-width: 76rem; margin: 0 auto; }
 h1 { font-size: 1.6rem; margin-bottom: .2rem; }
 h2 { font-size: 1.15rem; margin-top: 2.5rem; padding-top: 1.2rem;
      border-top: 1px solid #e6e6e6; }
 h4 { margin: 1rem 0 .35rem; font-size: .78rem; text-transform: uppercase;
      letter-spacing: .05em; color: #1a4480; }
 .lede { color: #555; }
 pre { background: #f7f8fa; border: 1px solid #e6e6e6; border-radius: 6px; padding: .8rem;
       white-space: pre-wrap; word-wrap: break-word; font-size: .78rem; line-height: 1.45; }
 table.dataTable td { vertical-align: top; font-size: .88rem; }
 td.expand-control { cursor: pointer; width: 2rem; color: #1a4480; }
 .expand-icon { display: inline-block; transition: transform .15s; }
 .expand-icon.expanded { transform: rotate(90deg); }
 .child-panel { background: #fbfcfd; padding: 1rem 1.2rem; }
 .posting { border-left: 3px solid #dfe6ef; padding-left: .8rem; margin: .6rem 0; }
 .posting p { margin: .3rem 0 0; font-size: .84rem; color: #333; }
 .agency { color: #777; font-size: .78rem; margin-left: .5rem; }
 .ok { color: #2a6f3b; } .miss { color: #a33; }
 table.ratings { width: 100%; border-collapse: collapse; font-size: .85rem; }
 table.ratings td { padding: .22rem .5rem; border-top: 1px solid #eee; }
 td.s { width: 2rem; text-align: center; font-weight: 700; }
 .qid { width: 2.4rem; color: #999; font-size: .78rem; text-align: right; }
 .origin { width: 5rem; color: #777; font-size: .8rem; }
 .s0 { color: #ccc; } .s1 { color: #999; } .s2 { color: #444; }
 .s3 { color: #1a4480; } .s4 { color: #1a4480; background: #eef3fa; }
 .item { border-left: 3px solid #dfe6ef; padding-left: .8rem; margin: .6rem 0; }
 .item p { margin: 0; font-size: .85rem; }
 .meta { color: #777; font-size: .75rem; }
</style></head><body><div class="wrap">

<h1>Every LLM call behind the quiz</h1>
<p class="lede">__N__ occupations, __MATCHED__ of them matched to the exact cached response.
Each prompt is rebuilt from the code and hashed the way the pipeline hashes it; the key
shown is where its response was found, so the pairing is checked rather than asserted.</p>

<h2>Rating calls — <code>__RATE_MODEL__</code>, temperature 0</h2>
<h4>System prompt, identical for every occupation</h4>
<pre>__RATE_SYSTEM__</pre>

<table id="calls" class="table table-hover" style="width:100%">
  <thead><tr>
    <th></th><th>Occupation</th><th>Series</th><th>Hires/yr</th>
    <th>Postings</th><th>Hiring agency</th><th>Work family</th>
    <th>Education needed</th><th>Response</th>
  </tr></thead>
  <tbody></tbody>
</table>

<h2>The __NQ__ questions</h2>
<table class="table"><tbody>__QROWS__</tbody></table>

<h2>Generation calls — <code>__GEN_MODEL__</code></h2>
<h4>System prompt</h4>
<pre>__GEN_SYSTEM__</pre>
<p class="lede">The cache stores responses keyed by a hash of the request, and a generation
prompt depends on which occupations were batched together on that run, so these responses
are not paired with their prompts.</p>
<div id="gens"></div>

</div>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
<script src="shared/shared.js"></script>
<script>
const ROWS = __ROWS__;
const GENS = __GENS__;

function esc(s) { return $('<div>').text(s == null ? '' : s).html(); }

// One row per occupation; the prompt and response hang off it as child content.
const tbody = document.querySelector('#calls tbody');
ROWS.forEach((r) => {
  const status = r.paired === 'matched'
    ? `<span class="ok">matched</span>` : `<span class="miss">no response</span>`;
  const tr = createExpandableParentRow({
    caseDisplayName: r.name,
    childData: r,
    cells: [
      r.name, r.series, r.hires.toLocaleString(), String(r.postings),
      r.agency, r.family, r.degree,
      { element: $(status)[0] },
    ],
  });
  tbody.appendChild(tr);
});

function buildChildContent(r) {
  const postings = r.sample.length
    ? r.sample.map((d) => `<div class="posting"><b>${esc(d.title)}</b>`
        + `<span class="agency">${esc(d.agency)}</span><p>${esc(d.duties)}</p></div>`).join('')
    : '<p class="text-muted fst-italic">No posting text available.</p>';
  const ratings = r.ratings.map((x) =>
    `<tr><td class="s s${x.s == null ? 0 : x.s}">${x.s == null ? '\\u2014' : x.s}</td>`
    + `<td class="qid">${x.i}</td><td>${esc(x.q)}</td></tr>`).join('');
  return `<div class="child-panel">
      <p class="meta">${r.paired === 'matched'
        ? '<span class="ok">cache hit</span>' : '<span class="miss">no cached response</span>'}
        \\u00b7 ${esc(r.key)}</p>
      <h4>Postings sampled for this occupation</h4>${postings}
      <h4>User prompt, exactly as sent</h4><pre>${esc(r.prompt)}</pre>
      <h4>Response</h4><table class="ratings">${ratings}</table>
      ${r.raw ? '<h4>Response, raw</h4><pre>' + esc(r.raw) + '</pre>' : ''}
    </div>`;
}

const { table } = initDataTableWithFilters({
  tableSelector: '#calls',
  tableOptions: {
    pageLength: 25,
    order: [[1, 'asc']],
    columnDefs: [
      { targets: 0, orderable: false, searchable: false },
      { targets: [5, 6], visible: true },
    ],
  },
  fieldTypes: { agency: 'multiselect', family: 'multiselect', degree: 'multiselect',
                paired: 'multiselect', hires: 'range', name: 'text' },
  columns: [
    { index: 1, field: 'name', label: 'Occupation' },
    { index: 3, field: 'hires', label: 'Hires per year' },
    { index: 5, field: 'agency', label: 'Hiring agency' },
    { index: 6, field: 'family', label: 'Work family' },
    { index: 7, field: 'degree', label: 'Education needed' },
    { index: 8, field: 'paired', label: 'Response' },
  ],
  filterBarId: 'filtersBar',
  csvDownload: false,
});

setupDataTableExpandHandlers({ tableSelector: '#calls', buildChildContent });

document.getElementById('gens').innerHTML = GENS.map((g) => {
  const head = g.subject.slice(0, 4).join(', ')
    + (g.subject.length > 4 ? ` +${g.subject.length - 4} more` : '');
  return `<details><summary><b>${esc(head) || 'batch'}</b> \\u00b7 ${g.items.length} items</summary>
    <p class="meta"><span class="miss">prompt not recoverable</span> \\u00b7 response cached at ${esc(g.key)}</p>
    <h4>Occupations these items were written to separate</h4>
    <p class="meta">${esc(g.subject.join(', '))}</p>`
    + g.items.map((it) => `<div class="item"><p>${esc(it.text)}</p>`
        + `<span class="meta">axis: ${esc(it.axis)} \\u00b7 separates: ${esc(it.separates)}</span></div>`).join('')
    + `</details>`;
}).join('');
</script>
</body></html>
"""
