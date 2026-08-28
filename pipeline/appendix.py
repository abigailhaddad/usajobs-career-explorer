#!/usr/bin/env python3
"""Build the browsable appendix: every LLM call behind the quiz.

Rating calls are reconstructed from the code and then verified: the prompt is
rebuilt, hashed the way pipeline/llm.py hashes it, and matched against the
response sitting at that key in data/.llm_cache. A row only claims to be a
matched pair if the key resolves, so nothing here is a paraphrase of a call.

Generation responses are shown too, but the cache is keyed by hash with no
stored prompt, and a generation prompt depends on which 25 occupations were
batched together on that run. Those are labelled as unpaired.

    Written by stage 4, after site/data.json.
"""
import html
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

from . import llm, s5_questions as s5
from .config import DATA, SITE

CACHE = DATA / ".llm_cache"


def esc(x):
    return html.escape(str(x or ""))


def rating_calls(cfg, facts, questions, text_by_series):
    """Rebuild every rating prompt and pair it with its cached response."""
    mc = cfg["model"]
    smax = cfg["rating"]["scale_max"]
    system = cfg["rating"]["prompt"]
    schema = s5.RatingSet.model_json_schema()
    qlist = "\n".join(f"{i}. {t}" for i, t in enumerate(questions))
    out = []
    for r in facts.itertuples():
        blurb = s5._occ_blurb(r, text_by_series)
        prompt = (f"Occupation:\n{blurb}\n\n"
                  f"Score every statement 0-{smax}. "
                  f"Return one entry per statement id.\n\n{qlist}")
        key = llm._key(mc["rate"], system, prompt, schema)
        path = CACHE / f"{key}.json"
        resp = json.loads(path.read_text()) if path.exists() else None
        out.append({"row": r, "prompt": prompt, "key": key, "resp": resp,
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
        if isinstance(d, dict) and d.get("questions") and isinstance(d["questions"], list):
            if isinstance(d["questions"][0], dict) and "axis" in d["questions"][0]:
                found.append((f.stem, d["questions"]))
        if len(found) >= limit:
            break
    return found


def family_labels(facts):
    """Short name per work family, from its two biggest hirers."""
    import numpy as np
    lab_path, idx_path = DATA / "llm_labels_12.npy", DATA / "llm_families_index.csv"
    if not (lab_path.exists() and idx_path.exists()):
        return {}, {}
    lab = np.load(lab_path)
    idx = pd.read_csv(idx_path, dtype={"series": str})
    if list(idx.series) != list(facts.series):
        return {}, {}
    names = {}
    for c in sorted(set(lab)):
        members = [s.strip() for s in
                   facts[lab == c].nlargest(2, "hires_entry_perm").series_name]
        names[c] = " / ".join(members)
    return dict(zip(facts.series, (names[c] for c in lab))), names


def run(out: Path = None):
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
    fam_of, _ = family_labels(facts)

    qrows = "".join(
        f'<tr><td class="qid">{i}</td><td>{esc(t)}</td><td class="origin">{esc(o)}</td></tr>'
        for i, (t, o) in enumerate(zip(questions, origins)))

    occ_rows = []
    for c in calls:
        r, resp = c["row"], c["resp"]
        if resp:
            scores = {x["question_id"]: x["score"] for x in resp["ratings"]}
            ranked = sorted(((scores.get(i, 0), i, q) for i, q in enumerate(questions)),
                            key=lambda p: (-p[0], p[1]))
            rating_html = '<table class="ratings">' + "".join(
                f'<tr><td class="s s{v}">{v}</td><td class="qid">{i}</td><td>{esc(q)}</td></tr>'
                for v, i, q in ranked) + "</table>"
            raw = json.dumps(resp, separators=(",", ":"))
            status = f'<span class="ok">cache hit · {c["key"]}</span>'
        else:
            rating_html = '<p class="none">No cached response at this key. The prompt below is what would be sent.</p>'
            raw = ""
            status = f'<span class="miss">no cached response · {c["key"]}</span>'

        postings = "".join(
            f'<div class="posting"><b>{esc(d.get("title"))}</b>'
            f'<span class="agency">{esc(d.get("agency"))}</span>'
            f'<p>{esc((d.get("duties") or d.get("summary") or "")[:900])}</p></div>'
            for d in c["sample"]) or '<p class="none">No posting text available.</p>'

        agencies = sorted({d.get("agency") for d in c["sample"] if d.get("agency")})
        fam = fam_of.get(r.series, "")
        occ_rows.append(
            f'<details class="occ" data-name="{esc((r.series_name + " " + r.series).lower())}"'
            f' data-hires="{int(r.hires_per_year)}"'
            f' data-agency="{esc("|".join(agencies))}"'
            f' data-family="{esc(fam)}"'
            f' data-degree="{esc(r.degree_requirement)}"'
            f' data-text="{"y" if c["sample"] else "n"}">'
            f'<summary><span class="nm">{esc(r.series_name)}</span>'
            f'<span class="ser">{esc(r.series)}</span>'
            f'<span class="hp">{int(r.hires_per_year):,}/yr</span>'
            f'<span class="np">{len(c["sample"])} posting{"" if len(c["sample"]) == 1 else "s"}</span>'
            f'</summary>'
            f'<p class="status">{status}</p>'
            f'<h4>Postings sampled for this occupation</h4>{postings}'
            f'<h4>User prompt, exactly as sent</h4><pre>{esc(c["prompt"])}</pre>'
            f'<h4>Response</h4>{rating_html}'
            + (f'<h4>Response, raw</h4><pre class="raw">{esc(raw)}</pre>' if raw else "")
            + '</details>')

    def batch_subject(items):
        """Which occupations a generation batch was about.

        The cache stores responses only, so the prompt that produced a batch is
        gone. Each item names the pair it was written to separate, which is
        enough to say what the batch covered.
        """
        seen = []
        for it in items:
            for part in str(it.get("separates") or "").split(" vs "):
                name = part.strip(" .;")
                if name and name not in seen:
                    seen.append(name)
        return seen

    gens = generation_responses()
    gen_html = ""
    for k, items in gens:
        subj = batch_subject(items)
        head = ", ".join(subj[:4]) + (f" +{len(subj) - 4} more" if len(subj) > 4 else "")
        gen_html += (
            f'<details class="gen" data-name="{esc(" ".join(subj).lower())}">'
            f'<summary><span class="nm">{esc(head) or "batch"}</span>'
            f'<span class="np">{len(items)} items</span></summary>'
            f'<p class="status"><span class="miss">prompt not recoverable</span> '
            f'· response cached at {esc(k)}</p>'
            f'<h4>Occupations these items were written to separate</h4>'
            f'<p class="subj">{esc(", ".join(subj))}</p>'
            + "".join(f'<div class="item"><p>{esc(it.get("text"))}</p>'
                      f'<span class="meta">axis: {esc(it.get("axis"))} · '
                      f'separates: {esc(it.get("separates"))}</span></div>' for it in items)
            + "</details>")

    def facet(name, values, label):
        opts = "".join(
            f'<label class="opt"><input type="checkbox" data-facet="{name}" '
            f'value="{esc(v)}"><span>{esc(v)}</span><i>{n}</i></label>'
            for v, n in values)
        return (f'<div class="facet"><button class="facet-btn" data-for="{name}">'
                f'{label}<span class="caret">\u25be</span></button>'
                f'<div class="facet-menu" id="menu-{name}">'
                f'<div class="facet-title">{label}</div>'
                f'<input class="facet-search" type="search" placeholder="Search\u2026" '
                f'data-search="{name}" autocomplete="off">'
                f'<div class="facet-options">{opts}</div>'
                f'<div class="facet-buttons">'
                f'<button class="btn-clear" data-clear="{name}">Clear</button></div>'
                f'</div></div>')

    from collections import Counter
    ag = Counter(a for c in calls for a in
                 sorted({d.get("agency") for d in c["sample"] if d.get("agency")}))
    fam = Counter(fam_of.get(r.series, "") for r in facts.itertuples() if fam_of.get(r.series))
    deg = Counter(r.degree_requirement for r in facts.itertuples())
    facets = (facet("agency", ag.most_common(), "Hiring agency")
              + facet("family", sorted(fam.items()), "Work family")
              + facet("degree", sorted(deg.items()), "Education needed"))

    page = (TEMPLATE
            .replace("__FACETS__", facets)
            .replace("__OCC_ROWS__", "\n".join(occ_rows))
            .replace("__QROWS__", qrows)
            .replace("__GEN__", gen_html or '<p class="none">No generation batches in the cache.</p>')
            .replace("__N__", f"{len(calls):,}")
            .replace("__MATCHED__", f"{matched:,}")
            .replace("__NQ__", str(len(questions)))
            .replace("__RATE_MODEL__", esc(cfg["model"]["rate"]))
            .replace("__GEN_MODEL__", esc(cfg["model"]["generate"]))
            .replace("__RATE_SYSTEM__", esc(cfg["rating"]["prompt"].strip()))
            .replace("__GEN_SYSTEM__", esc(gen_system(cfg))))
    out.write_text(page)
    print(f"wrote {out}")
    print(f"  {len(calls)} occupations, {matched} matched to a cached response, "
          f"{len(gens)} generation batches, {out.stat().st_size/1024:.0f} KB")


def gen_system(cfg):
    """The generation system prompt, built the way stage 5 builds it."""
    gen = cfg["generation"]
    axes = "\n".join(f"- {a}" for a in gen["axes"])
    return (f"{gen['system'].strip()}\n\n"
            f"Draw items from these axes:\n{axes}\n\n{gen['style']}\n\n{gen['avoid']}")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Every LLM call behind the quiz</title>
<style>
 body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
        max-width: 62rem; margin: 2.5rem auto; padding: 0 1.25rem; color: #1b1b1b; line-height: 1.5; }
 h1 { font-size: 1.6rem; margin-bottom: .2rem; }
 h2 { font-size: 1.15rem; margin-top: 2.5rem; border-top: 1px solid #e6e6e6; padding-top: 1.2rem; }
 h4 { margin: 1.1rem 0 .35rem; font-size: .78rem; text-transform: uppercase;
      letter-spacing: .05em; color: #1a4480; }
 .lede { color: #555; margin-top: 0; }
 pre { background: #f7f8fa; border: 1px solid #e6e6e6; border-radius: 6px; padding: .8rem;
       white-space: pre-wrap; word-wrap: break-word; font-size: .78rem; line-height: 1.45; }
 pre.raw { font-size: .72rem; color: #444; }
 /* Controls — search on its own line, facets under it, chips under those. */
 #q { width: 100%; padding: .7rem .9rem; font-size: 1rem; border: 1px solid #ccc;
      border-radius: 9px; margin: 1.2rem 0 .6rem; box-sizing: border-box; }
 #q:focus { outline: none; border-color: #1a4480; }
 .facet-row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
 .result-row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin: .2rem 0 1rem; }
 .spacer { flex: 1; }
 #count { color: #666; font-size: .85rem; margin: 0; }
 .toggle { font-size: .85rem; color: #444; display: inline-flex; gap: .4rem;
           align-items: center; white-space: nowrap; }
 select, #expand, #collapse { padding: .45rem .7rem; font-size: .85rem; border: 1px solid #ccc;
                              border-radius: 8px; background: #fff; cursor: pointer; }
 #expand:hover, #collapse:hover { border-color: #1a4480; color: #1a4480; }

 /* Facet buttons open a card of checkboxes, one open at a time. */
 .facet { position: relative; }
 .facet-btn { display: inline-flex; align-items: center; gap: .4rem; padding: .45rem .8rem;
              font-size: .85rem; font-weight: 600; border: 1px solid #ccc; border-radius: 999px;
              background: #fff; color: #333; cursor: pointer; white-space: nowrap; }
 .facet-btn:hover { border-color: #1a4480; color: #1a4480; }
 .facet-btn.on { background: #1a4480; border-color: #1a4480; color: #fff; }
 .caret { font-size: .7rem; opacity: .7; }
 .facet-menu { display: none; position: absolute; z-index: 30; top: calc(100% + .4rem); left: 0;
               width: 22rem; max-width: 88vw; background: #fff; border-radius: 14px;
               box-shadow: 0 10px 30px rgba(0,0,0,.22); padding: 1rem; }
 .facet-menu.open { display: block; }
 .facet-title { font-size: .9rem; font-weight: 700; margin-bottom: .7rem; padding-bottom: .6rem;
                border-bottom: 2px solid #eef3fa; }
 .facet-search { width: 100%; padding: .5rem .65rem; border: 1px solid #ccc; border-radius: 8px;
                 font-size: .85rem; margin-bottom: .5rem; box-sizing: border-box; }
 .facet-search:focus { outline: none; border-color: #1a4480; }
 .facet-options { display: flex; flex-direction: column; gap: 1px; max-height: 17rem;
                  overflow-y: auto; }
 .facet-menu .opt { display: flex; align-items: center; gap: .55rem; padding: .4rem .4rem;
                    font-size: .85rem; border-radius: 6px; cursor: pointer; }
 .facet-menu .opt:hover { background: #f3f4f6; }
 /* display:flex above beats the hidden attribute, so say it explicitly. */
 .facet-menu .opt[hidden] { display: none; }
 .facet-menu .opt span { flex: 1; }
 .facet-menu .opt i { color: #888; font-style: normal; font-size: .78rem; margin-left: auto; }
 .facet-menu .opt input { width: 15px; height: 15px; accent-color: #1a4480; cursor: pointer; }
 .facet-buttons { display: flex; justify-content: flex-end; margin-top: .7rem;
                  padding-top: .6rem; border-top: 1px solid #eee; }
 .btn-clear { border: 1px solid #ccc; background: #fff; color: #666; padding: .35rem .8rem;
              border-radius: 8px; cursor: pointer; font-size: .8rem; }
 .btn-clear:hover { border-color: #1a4480; color: #1a4480; }

 /* Active filters live in a bar of their own, like the other dashboards. */
 .filters-bar { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem;
                min-height: 46px; margin: .7rem 0 .2rem; background: #fafbfc;
                border: 1px solid #e6e6e6; border-radius: 12px; padding: .5rem .75rem; }
 .filters-bar-empty { color: #9a9a9a; font-size: .82rem; }
 .filter-chip { display: inline-flex; align-items: center; gap: 7px; background: #eef3fa;
                border: 1px solid #1a4480; border-radius: 999px; padding: 4px 6px 4px 12px;
                font-size: .78rem; color: #1a4480; }
 .filter-chip-label { font-weight: 700; }
 .filter-chip-value { max-width: 17rem; overflow: hidden; text-overflow: ellipsis;
                      white-space: nowrap; }
 .filter-chip-remove { cursor: pointer; font-weight: 700; width: 18px; height: 18px;
                       display: flex; align-items: center; justify-content: center;
                       border-radius: 50%; }
 .filter-chip-remove:hover { background: #1a4480; color: #fff; }
 .chip-clear { background: none; border: 0; color: #1a4480; font-size: .78rem;
               cursor: pointer; text-decoration: underline; padding: 0 .3rem; }
               cursor: pointer; text-decoration: underline; padding: 0 .3rem; }
 details.occ, details.gen { border-top: 1px solid #eee; padding: .35rem 0; }
 details.occ summary, details.gen summary { cursor: pointer; display: flex; gap: .6rem;
                                            align-items: baseline; }
 .nm { font-weight: 600; flex: 1; }
 .ser, .hp, .np { color: #777; font-size: .8rem; font-variant-numeric: tabular-nums; }
 .status { font-size: .75rem; margin: .5rem 0 0; }
 .ok { color: #2a6f3b; } .miss { color: #a33; }
 .posting { border-left: 3px solid #e0e0e0; padding-left: .8rem; margin: .6rem 0; }
 .posting p { margin: .3rem 0 0; font-size: .84rem; color: #333; }
 .agency { color: #777; font-size: .78rem; margin-left: .5rem; }
 .none { color: #777; font-style: italic; }
 table { border-collapse: collapse; width: 100%; font-size: .85rem; }
 table td, table th { padding: .25rem .5rem; border-top: 1px solid #eee; vertical-align: top;
                      text-align: left; }
 td.s { width: 2rem; text-align: center; font-weight: 700; }
 td.qid, .qid { width: 2rem; color: #999; font-size: .78rem; text-align: right; }
 .origin { width: 4rem; color: #777; font-size: .78rem; }
 .s0 { color: #ccc; } .s1 { color: #999; } .s2 { color: #444; }
 .s3 { color: #1a4480; } .s4 { color: #1a4480; background: #eef3fa; }
 .item { border-left: 3px solid #e0e0e0; padding-left: .8rem; margin: .6rem 0; }
 .item p { margin: 0; font-size: .85rem; }
 .subj { font-size: .85rem; color: #444; margin: .2rem 0 .6rem; }
 .meta { color: #777; font-size: .75rem; }
</style></head><body>

<h1>Every LLM call behind the quiz</h1>
<p class="lede">__N__ occupations, __MATCHED__ of them matched to the exact cached response.
Each prompt below is rebuilt from the code and hashed the way the pipeline hashes it; the
key shown is where its response was found, so the pairing is checked rather than asserted.</p>

<h2>The __NQ__ questions</h2>
<table>__QROWS__</table>

<h2>Rating calls — <code>__RATE_MODEL__</code>, temperature 0</h2>
<h4>System prompt, identical for every occupation</h4>
<pre>__RATE_SYSTEM__</pre>

<input id="q" type="search" placeholder="Search an occupation or series number…" autocomplete="off">

<div class="facet-row">
  __FACETS__
  <label class="toggle"><input type="checkbox" id="hastext"> Only with posting text</label>
  <span class="spacer"></span>
  <select id="sort">
    <option value="name">Sort: name</option>
    <option value="hires">Sort: hires per year</option>
  </select>
</div>

<div id="chips" class="filters-bar"><span id="chips-empty" class="filters-bar-empty">No filters applied</span></div>

<div class="result-row">
  <p id="count"></p>
  <span class="spacer"></span>
  <button id="expand">Expand all</button>
  <button id="collapse">Collapse all</button>
</div>
<div id="list">__OCC_ROWS__</div>

<h2>Generation calls — <code>__GEN_MODEL__</code></h2>
<h4>System prompt</h4>
<pre>__GEN_SYSTEM__</pre>
<p class="lede">The cache stores responses keyed by a hash of the request, and a generation
prompt depends on which occupations were batched together on that run, so these responses
are not paired with their prompts. Items that survived pruning are in the table above.</p>
__GEN__

<script>
 // Flat and dumb on purpose: one object holds the active filters, one function
 // applies them, one function draws the chips. Nothing else writes to state.
 const list = document.getElementById('list');
 const rows = [...list.querySelectorAll('details.occ')];
 const q = document.getElementById('q');
 const sort = document.getElementById('sort');
 const hastext = document.getElementById('hastext');
 const count = document.getElementById('count');
 const chipBar = document.getElementById('chips');
 const chipsEmpty = document.getElementById('chips-empty');

 const active = { agency: new Set(), family: new Set(), degree: new Set() };
 const LABEL = { agency: 'Agency', family: 'Family', degree: 'Education' };

 function matches(r) {
   const needle = q.value.trim().toLowerCase();
   if (needle && !r.dataset.name.includes(needle)) return false;
   if (hastext.checked && r.dataset.text !== 'y') return false;
   // Within a facet the values are OR'd; across facets they are AND'd.
   if (active.agency.size) {
     const mine = (r.dataset.agency || '').split('|');
     if (![...active.agency].some((a) => mine.includes(a))) return false;
   }
   if (active.family.size && !active.family.has(r.dataset.family)) return false;
   if (active.degree.size && !active.degree.has(r.dataset.degree)) return false;
   return true;
 }

 function drawChips() {
   chipBar.querySelectorAll('.filter-chip, .chip-clear').forEach((c) => c.remove());
   const chips = [];
   for (const facet of ['agency', 'family', 'degree']) {
     for (const v of active[facet]) chips.push([facet, v]);
   }
   if (q.value.trim()) chips.push(['search', q.value.trim()]);
   if (hastext.checked) chips.push(['text', 'has posting text']);
   chipsEmpty.style.display = chips.length ? 'none' : '';

   for (const [facet, v] of chips) {
     const el = document.createElement('div');
     el.className = 'filter-chip';
     el.innerHTML = '<span class="filter-chip-label"></span>'
                  + '<span class="filter-chip-value"></span>'
                  + '<span class="filter-chip-remove">\u00d7</span>';
     el.querySelector('.filter-chip-label').textContent = (LABEL[facet] || '') + (LABEL[facet] ? ':' : '');
     el.querySelector('.filter-chip-value').textContent = v;
     el.querySelector('.filter-chip-remove').addEventListener('click', () => {
       if (facet === 'search') { q.value = ''; }
       else if (facet === 'text') { hastext.checked = false; }
       else {
         active[facet].delete(v);
         const box = document.querySelector(
           `input[data-facet="${facet}"][value="${CSS.escape(v)}"]`);
         if (box) box.checked = false;
       }
       apply();
     });
     chipBar.append(el);
   }
   if (chips.length) {
     const clear = document.createElement('button');
     clear.className = 'chip-clear';
     clear.textContent = 'Clear all';
     clear.addEventListener('click', () => {
       q.value = '';
       hastext.checked = false;
       for (const f of ['agency', 'family', 'degree']) active[f].clear();
       document.querySelectorAll('input[data-facet]').forEach((b) => { b.checked = false; });
       apply();
     });
     chipBar.append(clear);
   }
 }

 function apply() {
   let shown = 0;
   for (const r of rows) {
     const hit = matches(r);
     r.hidden = !hit;
     if (hit) shown++;
   }
   count.textContent = shown + ' of ' + rows.length + ' occupations';
   for (const btn of document.querySelectorAll('.facet-btn')) {
     btn.classList.toggle('on', active[btn.dataset.for].size > 0);
   }
   drawChips();
 }

 function reorder() {
   const by = sort.value;
   const sorted = [...rows].sort((a, b) => by === 'hires'
     ? (+b.dataset.hires) - (+a.dataset.hires)
     : a.dataset.name.localeCompare(b.dataset.name));
   for (const r of sorted) list.append(r);
 }

 document.querySelectorAll('.facet-btn').forEach((btn) => {
   btn.addEventListener('click', (e) => {
     e.stopPropagation();
     const menu = document.getElementById('menu-' + btn.dataset.for);
     const wasOpen = menu.classList.contains('open');
     document.querySelectorAll('.facet-menu').forEach((m) => m.classList.remove('open'));
     menu.classList.toggle('open', !wasOpen);
   });
 });
 document.querySelectorAll('.facet-menu').forEach((m) => {
   m.addEventListener('click', (e) => e.stopPropagation());
 });
 document.addEventListener('click', () => {
   document.querySelectorAll('.facet-menu').forEach((m) => m.classList.remove('open'));
 });
 document.querySelectorAll('input[data-facet]').forEach((box) => {
   box.addEventListener('change', () => {
     const set = active[box.dataset.facet];
     if (box.checked) set.add(box.value); else set.delete(box.value);
     apply();
   });
 });

 // Long facet lists (there are a lot of agencies) get their own search.
 document.querySelectorAll('.facet-search').forEach((box) => {
   box.addEventListener('input', () => {
     const needle = box.value.trim().toLowerCase();
     const menu = document.getElementById('menu-' + box.dataset.search);
     for (const opt of menu.querySelectorAll('.opt')) {
       opt.hidden = needle && !opt.textContent.toLowerCase().includes(needle);
     }
   });
 });
 document.querySelectorAll('.btn-clear').forEach((btn) => {
   btn.addEventListener('click', () => {
     const facet = btn.dataset.clear;
     active[facet].clear();
     document.querySelectorAll(`input[data-facet="${facet}"]`).forEach((b) => { b.checked = false; });
     apply();
   });
 });

 q.addEventListener('input', apply);
 hastext.addEventListener('change', apply);
 sort.addEventListener('change', reorder);
 document.getElementById('expand').addEventListener('click',
   () => rows.forEach((r) => { if (!r.hidden) r.open = true; }));
 document.getElementById('collapse').addEventListener('click',
   () => rows.forEach((r) => { r.open = false; }));
 apply();
</script>
</body></html>
"""

