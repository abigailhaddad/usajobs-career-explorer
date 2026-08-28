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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css">
<link rel="stylesheet" href="shared/shared.css">
<style>
 /* Bootstrap ships cornflower blue; the site is navy. Recolour the components
    that show it rather than fighting each one where it appears. */
 :root {
   --navy: #1a4480; --navy-d: #12315e; --pale: #eef3fa; --line: #e3e7ec;
   --ink: #1b1b1b; --muted: #5d6470;
   --bs-primary: #1a4480; --bs-link-color: #1a4480; --bs-link-hover-color: #12315e;
   --sui-primary: #1a4480; --sui-accent: #1a4480; --sui-accent-light: #cfe0f5;
   --sui-accent-lighter: #eef3fa; --sui-bg-alt: #f7f8fa;
 }
 body { font: 16px/1.6 "Public Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        color: var(--ink); background: #fff; margin: 0; }
 .wrap { max-width: 78rem; margin: 0 auto; padding: 0 1.5rem 5rem; }

 /* Masthead */
 .masthead { background: var(--navy); color: #fff; padding: 2rem 0 1.6rem; margin-bottom: 0; }
 .masthead .wrap { padding-bottom: 0; }
 .masthead h1 { font-size: 1.55rem; font-weight: 700; margin: 0 0 .35rem; }
 .masthead p { margin: 0; color: #cfe0f5; font-size: .95rem; max-width: 54rem; }
 .masthead a { color: #fff; text-decoration: underline; }
 .stats { display: flex; gap: 2.2rem; margin-top: 1.1rem; flex-wrap: wrap; }
 .stat b { display: block; font-size: 1.35rem; line-height: 1.2; }
 .stat span { font-size: .78rem; color: #b8cdea; text-transform: uppercase; letter-spacing: .04em; }

 /* Tabs */
 .nav-tabs { border-bottom: 2px solid var(--line); margin: 1.6rem 0 1.4rem; gap: .2rem; }
 .nav-tabs .nav-link { border: 0; border-bottom: 3px solid transparent; border-radius: 0;
                       color: var(--muted); font-weight: 600; font-size: .92rem;
                       padding: .6rem 1rem; }
 .nav-tabs .nav-link:hover { color: var(--navy); border-bottom-color: #cfe0f5; }
 .nav-tabs .nav-link.active { color: var(--navy); border-bottom-color: var(--navy);
                              background: transparent; }
 .tab-note { color: var(--muted); font-size: .9rem; margin: 0 0 1rem; max-width: 56rem; }

 h2 { font-size: 1.1rem; font-weight: 700; margin: 1.8rem 0 .5rem; }
 h4 { margin: 1.1rem 0 .35rem; font-size: .72rem; text-transform: uppercase;
      letter-spacing: .06em; color: var(--navy); font-weight: 700; }
 pre { background: #f7f8fa; border: 1px solid var(--line); border-radius: 8px; padding: .9rem;
       white-space: pre-wrap; word-wrap: break-word; font-size: .78rem; line-height: 1.5;
       color: #2a2f36; }

 /* Table */
 table.dataTable { border-collapse: separate !important; }
 table.dataTable thead th { background: #f7f8fa; border-bottom: 2px solid var(--line) !important;
                            font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
                            color: var(--muted); font-weight: 700; }
 table.dataTable td { vertical-align: top; font-size: .88rem; border-top: 1px solid #f0f2f5; }
 table.dataTable tbody tr.shown { background: var(--pale); }
 td.expand-control { cursor: pointer; width: 2.2rem; color: var(--navy); font-size: .8rem; }
 .expand-icon { display: inline-block; transition: transform .15s; }
 .expand-icon.expanded { transform: rotate(90deg); }
 .num { font-variant-numeric: tabular-nums; }

 /* Pagination and controls, in navy */
 .page-link { color: var(--navy); border-color: var(--line); }
 .page-link:hover { background: var(--pale); color: var(--navy-d); }
 .page-item.active .page-link { background: var(--navy); border-color: var(--navy); color: #fff; }
 .page-item.disabled .page-link { color: #aab0b8; }
 .dataTables_info, .dataTables_length label { color: var(--muted); font-size: .85rem; }
 .form-select, .form-control { border-color: #ccd2da; }
 .form-select:focus, .form-control:focus { border-color: var(--navy);
                                           box-shadow: 0 0 0 .2rem rgba(26,68,128,.15); }
 .btn-primary { background: var(--navy); border-color: var(--navy); }
 .btn-primary:hover { background: var(--navy-d); border-color: var(--navy-d); }

 /* Expanded panel */
 .child-panel { background: #fbfcfd; padding: 1.1rem 1.3rem; border-left: 3px solid var(--navy); }
 .posting { border-left: 3px solid var(--line); padding-left: .85rem; margin: .7rem 0; }
 .posting p { margin: .3rem 0 0; font-size: .85rem; color: #333; }
 .agency { color: var(--muted); font-size: .78rem; margin-left: .5rem; }
 .ok { color: #2a6f3b; font-weight: 600; } .miss { color: #a33; font-weight: 600; }
 table.ratings { width: 100%; border-collapse: collapse; font-size: .85rem; }
 table.ratings td { padding: .24rem .55rem; border-top: 1px solid #eef0f3; }
 td.s { width: 2.2rem; text-align: center; font-weight: 700; }
 .qid { width: 2.6rem; color: #9aa1ab; font-size: .78rem; text-align: right; }
 .origin { width: 5rem; color: var(--muted); font-size: .8rem; }
 .s0 { color: #ccd1d8; } .s1 { color: #9aa1ab; } .s2 { color: #444; }
 .s3 { color: var(--navy); } .s4 { color: #fff; background: var(--navy); border-radius: 4px; }
 .item { border-left: 3px solid var(--line); padding-left: .85rem; margin: .7rem 0; }
 .item p { margin: 0; font-size: .85rem; }
 .meta { color: var(--muted); font-size: .76rem; }
 details.gen { border-top: 1px solid #f0f2f5; padding: .5rem 0; }
 details.gen summary { cursor: pointer; }
</style></head><body>

<header class="masthead"><div class="wrap">
  <h1>Every LLM call behind the quiz</h1>
  <p>The questions on the quiz were written by a language model, and every occupation was
  scored by one. This is all of it: the job postings each occupation was described by, the
  exact prompt, and the scores that came back.
  <a href="/">Back to the quiz</a></p>
  <div class="stats">
    <div class="stat"><b>__N__</b><span>occupations</span></div>
    <div class="stat"><b>__MATCHED__</b><span>matched to their cached response</span></div>
    <div class="stat"><b>__NQ__</b><span>questions</span></div>
  </div>
</div></header>

<div class="wrap">
<ul class="nav nav-tabs" role="tablist">
  <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab"
      data-bs-target="#tab-calls" type="button">Rating calls</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab"
      data-bs-target="#tab-questions" type="button">The questions</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab"
      data-bs-target="#tab-generation" type="button">Writing the questions</button></li>
</ul>

<div class="tab-content">
  <div class="tab-pane fade show active" id="tab-calls">
    <p class="tab-note">One row per occupation, rated by <code>__RATE_MODEL__</code> at
    temperature 0. Each prompt is rebuilt from the code and hashed the way the pipeline
    hashes it, so the key shown is where its response was found — the pairing is checked
    rather than asserted. Open a row to see the postings, the prompt and the scores.</p>
    <table id="calls" class="table table-hover" style="width:100%">
      <thead><tr>
        <th></th><th>Occupation</th><th>Series</th><th>Hires/yr</th>
        <th>Postings</th><th>Hiring agency</th><th>Work family</th>
        <th>Education needed</th><th>Response</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <div class="tab-pane fade" id="tab-questions">
    <p class="tab-note">The __NQ__ questions the quiz asks. Narrow ones are written to
    separate particular occupations; broad ones cover kinds of work the narrow ones miss.</p>
    <h4>System prompt used to score every occupation</h4>
    <pre>__RATE_SYSTEM__</pre>
    <table class="table"><tbody>__QROWS__</tbody></table>
  </div>

  <div class="tab-pane fade" id="tab-generation">
    <p class="tab-note">Candidate questions were written by <code>__GEN_MODEL__</code>, shown
    real occupations and their posting text. The cache stores responses keyed by a hash of
    the request, and a generation prompt depends on which occupations were batched together
    on that run, so these responses are not paired with their prompts.</p>
    <h4>System prompt</h4>
    <pre>__GEN_SYSTEM__</pre>
    <h2>Batches</h2>
    <div id="gens"></div>
  </div>
</div>

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
    // l length, r processing, t table, i info, p pagination — no 'f', which is
    // DataTables' free-text box. Searching stays on because the column filters
    // use it; the filtering people see goes through "+ Add Filter".
    dom: 'lrtip',
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
