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
  // Once every question has an answer, changing one shouldn't mean clicking
  // through all the rest to get back to the results.
  $('done').hidden = state.answers.filter(Boolean).length < state.questions.length;

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

// --- shareable answers -----------------------------------------------------
// Answers are 1-5, one digit each, so a whole run fits in a readable query
// string: ?a=5235412534...  Nothing else is encoded; the results are rebuilt by
// re-scoring, not stored.

function answersToParam(answers) {
  return answers.join('');
}

// Returns an array of answers, or null when the string cannot be trusted:
// wrong length (the question set changed since the link was made) or a digit
// outside the scale. A stale link starts the quiz over rather than scoring
// against questions the sender never saw.
function answersFromParam(raw, expected) {
  if (!raw || raw.length !== expected) return null;
  const answers = [...raw].map(Number);
  if (answers.some((v) => !(v >= 1 && v <= SCALE.length))) return null;
  return answers;
}

function putAnswersInURL() {
  const url = new URL(window.location.href);
  url.searchParams.set('a', answersToParam(state.answers));
  history.replaceState(null, '', url);
}

function clearAnswersFromURL() {
  const url = new URL(window.location.href);
  url.searchParams.delete('a');
  history.replaceState(null, '', url);
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
  credential_required: 'f-warn',
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

  // Every occupation carries one 0-4 rating per question. Showing them is the
  // only way to see why a job ranked where it did, and to disagree with it.
  const rated = state.questions
    .map((q, i) => ({ text: q.question_text, score: s.profile[i] }))
    .sort((a, b) => b.score - a.score)
    .map((r) => `<tr><td class="score">${r.score}</td><td>${r.text}</td></tr>`)
    .join('');

  el.innerHTML = `
    <h2><span>${s.series_name} <span class="muted">(series ${s.series})</span></span>
        <span class="rank">#${position}</span></h2>
    <p class="muted">${s.ce_description}</p>
    <div>${flags}</div>
    <div class="facts">
      <div class="fact"><b>${num(s.hires_per_year)}</b><span class="muted">hired at entry level in a typical year</span></div>
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
    <details><summary>How this job was rated, 0 to 4</summary>
      <p class="muted">0 means this kind of work isn't part of the job. 4 means
      it's most of what the job is.</p>
      <table class="ratings">${rated}</table>
    </details>
    ${live}
    ${s.live_jobs.length ? '' :
      `<p><a href="${s.job_url}" target="_blank" rel="noopener">Search USAJOBS for "${s.series_name}"</a></p>`}`;
  return el;
}

function showResults() {
  state.ranked = rank(state.answers);
  $('intro').hidden = true;
  $('quiz').hidden = true;
  $('results').hidden = false;
  putAnswersInURL();
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
  if (g.per_year) {
    $('trendline').textContent =
      `Governmentwide, the federal government hires about `
      + `${g.per_year.toLocaleString()} people a year at entry level.`;
  }
  const qc = $('qcount');
  if (qc) qc.textContent = data.questions.length;

  $('start').addEventListener('click', () => {
    $('intro').hidden = true;
    $('quiz').hidden = false;
    showQuestion();
  });
  $('done').addEventListener('click', showResults);
  $('back').addEventListener('click', () => {
    if (state.index > 0) { state.index -= 1; showQuestion(); }
  });
  // Back to the last question with every answer intact, so one answer can be
  // changed without redoing the quiz. Start over is the one that wipes them.
  $('edit').addEventListener('click', () => {
    state.index = state.questions.length - 1;
    clearAnswersFromURL();
    $('results').hidden = true;
    $('quiz').hidden = false;
    showQuestion();
  });
  $('retake').addEventListener('click', () => {
    state.answers = [];
    state.index = 0;
    clearAnswersFromURL();
    $('results').hidden = true;
    $('quiz').hidden = false;
    showQuestion();
  });
  $('copylink').addEventListener('click', async (e) => {
    await navigator.clipboard.writeText(window.location.href);
    e.target.textContent = 'Link copied';
    setTimeout(() => { e.target.textContent = 'Copy link to these results'; }, 2000);
  });

  // A link with answers in it goes straight to the results.
  const shared = answersFromParam(
    new URL(window.location.href).searchParams.get('a'), state.questions.length);
  if (shared) {
    state.answers = shared;
    showResults();
  }
  $('onlyOpen').addEventListener('change', drawCards);
  $('onlyReal').addEventListener('change', drawCards);
}

boot();
