// Federal Career Explorer with the fine print.
// Static page, no build step. Data comes from data.json, written by the pipeline.
//
// Scoring is the official algorithm, reimplemented from careerexplorerresults.js:
// z-score the answers, Pearson-correlate against each series' profile,
// sort descending. Verified against a live round-trip through usajobs.gov.

const SCALE = ['Not interested', 'Slightly interested', 'Moderately interested',
               'Very interested', 'Extremely interested'];

// One owner for mutable state. Nothing else writes to it.
const state = { questions: [], series: [], answers: [], index: 0, ranked: [] };

const $ = (id) => document.getElementById(id);

function pearson(a, b) {
  let sa = 0, sb = 0, sab = 0, saa = 0, sbb = 0;
  const n = a.length;
  for (let i = 0; i < n; i++) {
    sa += a[i]; sb += b[i]; sab += a[i] * b[i];
    saa += a[i] * a[i]; sbb += b[i] * b[i];
  }
  return (n * sab - sa * sb) / Math.sqrt((n * saa - sa * sa) * (n * sbb - sb * sb));
}

function zscore(v) {
  const mean = v.reduce((x, y) => x + y, 0) / v.length;
  const sd = Math.sqrt(v.reduce((x, y) => x + (y - mean) ** 2, 0) / v.length);
  return v.map((x) => (x - mean) / (sd + 1e-100));
}

function rank(answers) {
  const z = zscore(answers);
  // Identical answers to every question make every correlation 0/0 = NaN,
  // and NaN comparators quietly stop the sort from ranking. Treat as 0.
  return state.series
    .map((s) => {
      const fit = pearson(z, s.profile);
      return { s, fit: Number.isFinite(fit) ? fit : 0 };
    })
    .sort((a, b) => b.fit - a.fit);
}

// --- quiz ------------------------------------------------------------------

function showQuestion() {
  const q = state.questions[state.index];
  $('qtext').textContent = q.question_text;
  $('counter').textContent = `Question ${state.index + 1} of ${state.questions.length}`;
  $('progress').style.width = `${(state.index / state.questions.length) * 100}%`;
  $('back').disabled = state.index === 0;

  const opts = $('opts');
  opts.replaceChildren();  // fresh buttons each time, so old listeners go with them
  SCALE.forEach((label, i) => {
    const b = document.createElement('button');
    b.textContent = label;
    if (state.answers[state.index] === i + 1) b.classList.add('sel');
    b.addEventListener('click', () => answer(i + 1));
    opts.append(b);
  });
}

function answer(value) {
  state.answers[state.index] = value;
  if (state.index < state.questions.length - 1) {
    state.index += 1;
    showQuestion();
  } else {
    showResults();
  }
}

// --- results ---------------------------------------------------------------

const FLAG_TONE = {
  no_open_door: 'f-bad', dormant: 'f-bad', bulk_hiring: 'f-bad',
  nothing_open_now: 'f-warn', thin: 'f-warn', degree_required: 'f-warn',
  hiring_fell: 'f-bad', credential_required: 'f-warn',
  age_cap: 'f-warn', skews_older: 'f-warn',
};

function num(x) { return Number(x || 0).toLocaleString(); }

function card(entry, position) {
  const s = entry.s;
  const el = document.createElement('div');
  el.className = 'card';

  const flags = s.flags.map((f) =>
    `<span class="flag ${FLAG_TONE[f.code] || 'f-warn'}">${f.note}</span>`).join('');

  const titles = s.common_titles.length
    ? s.common_titles.map((t) => `<li>${t}</li>`).join('')
    : '<li class="muted">No recent postings that were open to the general public.</li>';

  const jobLi = (j) =>
    `<li><a href="https://www.usajobs.gov/job/${j.id}" target="_blank" rel="noopener">${j.title}</a>
     <span class="muted">closes ${j.closes}</span></li>`;
  // Short lists read inline; long ones would swamp the card, so they fold away.
  // One series has 404 postings open at once.
  const live = !s.live_jobs.length ? ''
    : s.live_jobs.length <= 8
      ? `<p><b>Open right now:</b></p><ul>${s.live_jobs.map(jobLi).join('')}</ul>`
      : `<details><summary><b>${s.live_jobs.length} openings right now</b></summary>
         <ul class="joblist">${s.live_jobs.map(jobLi).join('')}</ul></details>`;

  el.innerHTML = `
    <h2><span>${s.series_name} <span class="muted">(series ${s.series})</span></span>
        <span class="rank">#${position}</span></h2>
    <p class="muted">${s.ce_description}</p>
    <div>${flags}</div>
    <div class="facts">
      <div class="fact"><b>${num(s.hires_last12)}</b><span class="muted">hired at entry level in the last 12 months</span></div>
      <div class="fact"><b>${num(s.reachable_open_now)}</b><span class="muted">openings anyone can apply to right now</span></div>
    </div>
    <details><summary>Job titles and requirements</summary>
      <ul>${titles}</ul>
      <table>
        <tr><th>Typical entry grade</th><td>${s.typical_entry_grade === null ? '\u2014' : 'GS-' + s.typical_entry_grade}</td></tr>
        <tr><th>Education needed</th><td>${{
            degree: "A bachelor's degree",
            credential: "A licence or certificate, not usually a degree",
            none: 'No degree requirement',
            unknown: 'Too few hires to say',
          }[s.degree_requirement]}</td></tr>
        <tr><th>Permanent or temporary</th><td>${{
            'usually permanent': 'Usually permanent',
            'mixed': 'A mix of permanent and temporary',
            'usually temporary': 'Usually temporary or seasonal',
            'unknown': '\u2014',
          }[s.tenure_kind]}</td></tr>
      </table>
    </details>
    ${live}
    ${s.live_jobs.length ? '' :
      `<p><a href="${s.job_url}" target="_blank" rel="noopener">Search USAJOBS for "${s.series_name}"</a></p>`}`;
  return el;
}

function showResults() {
  state.ranked = rank(state.answers);
  $('quiz').hidden = true;
  $('results').hidden = false;
  drawCards();
}

function drawCards() {
  const onlyOpen = $('onlyOpen').checked;
  const onlyReal = $('onlyReal').checked;
  let shown = state.ranked;
  if (onlyOpen) shown = shown.filter((e) => e.s.reachable_open_now > 0);
  if (onlyReal) shown = shown.filter((e) => e.s.hires_per_year >= 50);

  const hidden = state.ranked.length - shown.length;
  $('filtered').textContent = hidden
    ? `Showing the top 15 of ${shown.length} matches. ${hidden} of your ${state.ranked.length} matches are hidden by the filters above.`
    : `Showing the top 15 of ${shown.length} matches.`;

  const box = $('cards');
  box.replaceChildren();
  shown.slice(0, 15).forEach((e, i) => box.append(card(e, i + 1)));
}

// --- boot ------------------------------------------------------------------

async function boot() {
  const res = await fetch('data.json');
  if (!res.ok) throw new Error(`data.json failed to load: ${res.status}`);
  const data = await res.json();
  state.questions = data.questions;
  state.series = data.series;
  const g = data.governmentwide || {};
  if (g.best12) {
    const pctOfPeak = Math.round((g.last12 / g.best12) * 100);
    $('trendline').textContent =
      `Governmentwide, permanent entry-level hiring in the last 12 months was `
      + `${g.last12.toLocaleString()} — ${pctOfPeak}% of its best 12 months `
      + `(${g.best12.toLocaleString()}).`;
  }
  const qc = $('qcount');
  if (qc) qc.textContent = data.questions.length;

  $('start').addEventListener('click', () => {
    $('intro').hidden = true;
    $('quiz').hidden = false;
    showQuestion();
  });
  $('back').addEventListener('click', () => {
    if (state.index > 0) { state.index -= 1; showQuestion(); }
  });
  $('retake').addEventListener('click', () => {
    state.answers = [];
    state.index = 0;
    $('results').hidden = true;
    $('quiz').hidden = false;
    showQuestion();
  });
  $('onlyOpen').addEventListener('change', drawCards);
  $('onlyReal').addEventListener('change', drawCards);
}

boot();
