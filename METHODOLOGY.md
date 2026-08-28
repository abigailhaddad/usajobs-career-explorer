# Methodology

Every prompt, dataset sample and formula below is copied from a real run. The
worked example follows one occupation, **practical nurse (series 0620)**, from
raw source to what ships in the browser.

Regenerate anything here with `python run.py` (stages 1, 2, 3, 7, 4) and
`python run.py --stages 5` (the questions).

---

## Sources

| stage | source | what it answers |
|---|---|---|
| s1 | `usajobs.gov/careerexplorer/quiz` page HTML | which occupations exist, and the official questions and profiles |
| s2 | `usajobs_historical` R2 bucket, `current_jobs_{2025,2026}.parquet` | what is posted, who may apply, what it asks for |
| s3 | HuggingFace `impactproject/opm-ehri-data` accessions | who actually got hired |
| s7 | OPM GS Qualification Standards, scraped in [opm-educ-req](https://github.com/abigailhaddad/opm-educ-req) | whether a degree is legally required |
| s4 | the four above | one row per occupation → `site/data.json` |
| s5 | LLM, opt-in | the 25 questions and every occupation's ratings |

---

## s1 — the official quiz

`data/questions.parquet` (32 rows × 3 cols) — `question_id, sort_order, question_text`

```
 question_id  sort_order  question_text
           1           1  Operate machines to cut, shape and fit metal, glass or concrete parts. Use tools to create finished products or structures.
           2           2  Fix electronics, machines and work equipment using hand and power tools. Replace parts, make repairs and perform tests to check the results.
           3           3  Study plant and animal growth in settings such as farms, forests, zoos or shelters. Tend to crops or animals and learn what affects their growth or survival.
```

`data/series_profiles.parquet` (302 rows × 5 cols) — `series, series_name, ce_description, related_titles, profile`

The official profile for 0620, first six of 32 floats:

```
[0.103429791, -0.132386898, -0.000247793, 3.028433097, 0.16298403, -1.277121255, ...]
```

---

## s2 — postings

Definitions, from `pipeline/config.py`:

```python
PUBLIC_PATHS = ("public", "The public", "student", "Students",
                "graduates", "Recent graduates")
GS_LIKE      = ("GS", "GG", "GL", "GW", "FG", "IM", "ND", "DB")
TRADE_PLANS  = ("WG", "WL")
ENTRY_MAX_GRADE = 9
```

The reachability test, from `pipeline/s2_openings.py`:

```python
public_path = (f"EXISTS (SELECT 1 FROM json_each(HiringPaths) h "
               f"WHERE json_extract_string(h.value,'$.hiringPath') IN ({_lst(PUBLIC_PATHS)}))")

# A grade number only means "junior" on GS-style plans. Banded plans number
# the other way: an IP-01 Deputy Director pays $151k, and 575 Senior
# Executive Service postings were being counted as entry level because ES
# grades read 01.
entry_grade = (f"((payScale IN ({gs_like}) AND {grade} IS NOT NULL "
               f"AND {grade} BETWEEN 1 AND {ENTRY_MAX_GRADE})"
               f" OR payScale IN ({trades}))")

reach = f"({public_path}) AND appointmentType='Permanent' AND {entry_grade}"
openings = "COALESCE(TRY_CAST(totalOpenings AS INT),1)"
```

`data/openings.parquet` (605 rows × 29 cols):

```
series  ann_total  ann_public  ann_reachable  openings_reachable  reachable_open_now          status  pct_degree_required
  0006        551          29              2                   2                   1        open_now                  0.0
  0007        886         162             83                4597                  11        open_now                  0.0
  0017         58          14              0                   0                   0 recently_active                  NaN
```

Series 0007 is why openings are counted rather than announcements: 83 reachable
announcements carry 4,597 openings.

---

## s3 — hires

From `pipeline/s3_hiring.py`:

```python
PERM  = "(tenure LIKE 'TENURE GROUP 1%' OR tenure LIKE 'TENURE GROUP 2%')"
ENTRY = (f"((pay_plan_code IN ({_sql_list(GS_LIKE)}) AND TRY_CAST(grade AS INT) "
         f"BETWEEN 1 AND {ENTRY_MAX_GRADE}) OR pay_plan_code IN ({_sql_list(TRADE_PLANS)}))")
```

```sql
sum(n) FILTER ({NEW})                                    AS hires_new,
sum(n) FILTER ({NEW} AND {PERM})                         AS hires_new_perm,
sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY})             AS hires_entry_perm,
sum(n) FILTER ({NEW} AND {ENTRY} AND NOT {PERM})         AS hires_entry_temp,
sum(n) FILTER ({NEW} AND {PERM} AND {ENTRY} AND {YOUNG}) AS hires_entry_perm_young,
```

`data/hires.parquet` (645 rows × 17 cols):

```
series  hires_entry_perm  hires_entry_temp  hires_new
     *               191                80       1257
  0006                 7                 1         56
  0007             10773                 2      10776
```

---

## s7 — qualification standards

`data/opm_standards.parquet` (415 rows × 4 cols):

```
series  opm_degree_required  opm_experience_alt  standard_chars
  0006                False               False            3661
  0007                False               False            7138
  0011                False               False            1869
```

---

## s4 — the join

`data/series_facts.parquet`, the row for practical nurse:

```
series                   0620
series_name              Practical nurse
hires_entry_perm         10525
hires_per_year           1914.0
status                   open_now
reachable_open_now       102.0
openings_reachable       1360.0
pct_degree_required      0.4
opm_degree_required      False
degree_requirement       credential
typical_entry_grade      3.0
tenure_kind              usually permanent
```

`pct_degree_required` is 0.4 and `opm_degree_required` is False, yet the answer
is `credential`. Four sources are combined, because postings restate
requirements inconsistently and about 6% of the education field is miscoded. Of
practical nurses, 9% hold a bachelor's and 87% hold some college, and the job
needs an LPN diploma.

The same occupation as it ships in `site/data.json`:

```json
{
 "series": "0620",
 "series_name": "Practical nurse",
 "hires_per_year": 1914,
 "reachable_open_now": 102,
 "degree_requirement": "credential",
 "tenure_kind": "usually permanent",
 "typical_entry_grade": 3,
 "profile": [0, 0, 0, 0, 1, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 1]
}
```

---

## s5 — writing the questions

Config: `pipeline/questions_config.yaml`. Generating model `gpt-5.4-mini`,
rating model `gpt-5.4-nano`, `temperature: 0.0`, `scale_max: 4`.

Targets: series with ≥250 permanent entry-grade hires since 2021, excluding
`never_reachable` and `dormant`, capped at 200. The last run used 175.

### Generation — system prompt

```
You write items for a career-matching instrument for federal jobs. The existing
instrument fails because its items do not separate the occupations that actually
hire: its 30 biggest entry-level hirers sit at 0.19 mean profile similarity, and
pairs like criminal investigating vs customs interdiction are indistinguishable
to it even though one hires thousands and the other hires nobody. Your items
must separate real jobs.

Draw items from these axes:
- Who you deal with all day: patients, inmates, taxpayers, travellers, soldiers, applicants, scientists, no one
- Where the work physically happens: hospital ward, forest, border, warehouse, courtroom, cubicle, aircraft hangar
- What passing through the door requires: a specific degree, a licence, a clearance, a fitness test, an age limit, nothing
- Rhythm and stakes: same task repeated to a standard, one long case at a time, emergencies, seasonal surges
- What a wrong call costs: money, a legal outcome, someone's health, someone's safety, nothing much

Write each item as a plain description of the activity, in the imperative, the
way a job description would put it — e.g. "Explain rules, benefits and next
steps to people worried about their own case, with every conversation
different." Two sentences at most. Never begin with "Would you rather", "Do you
like", "Are you interested in", or any question form. No question marks.

Do not write generic interest items ("working with data"). Do not write two
items that would score the same across these occupations. Do not ask about
skills or qualifications the person already has — describe the work itself, so
a 17-year-old can react to it. Every item must be one a real federal job would
answer differently from most others on the list.
```

The user message lists 25 real occupations, each rendered by `_occ_blurb` (the
same format shown under Rating below), and ends:

```
Write {per} items that would separate these occupations from each other.
(Independent attempt {n}: write a different set from what an obvious first pass
would produce.)
```

### Generation — response

Three of the items returned in one batch, verbatim from `data/.llm_cache`:

```json
{"questions": [
  {"text": "Walk a hospital ward or clinic floor and clean teeth, apply preventive treatments, and calm patients who are sitting in the chair for care. Follow the dentist's plan and adjust to each patient's mouth, pain, and anxiety.",
   "axis": "who you deal with all day",
   "separates": "Dental hygiene vs Laundry working"},
  {"text": "Sort, wash, dry, fold, and move soiled linen through the laundry side of a medical facility until it is ready for clean distribution. Keep the same processing pace hour after hour and make sure every load is handled to standard.",
   "axis": "rhythm and stakes",
   "separates": "Laundry working vs Public affairs"},
  {"text": "Stand at a border, port, or inspection point and examine travelers, cargo, or shipments before they pass through. Make quick calls that can affect safety, enforcement, and whether people or goods are allowed onward.",
   "axis": "where the work physically happens",
   "separates": "Agricultural commodity grading vs Fuel distribution system operating"}
]}
```

### Rating — system prompt

```
You are rating one federal occupation against a list of statements about work.
Use the real posting titles and qualification facts supplied, not your general
impression of the job title. Score how central each statement is to the everyday
work of someone hired into this occupation at entry level.
```

### Rating — user prompt

One real call, model `gpt-5.4-nano`, cache key `26b559d9bf6b6efa488215fd21b65e10`.
The occupation block is built by `_occ_blurb`; the statements are the 25 live
questions. Truncated at statement 8 only for length — the real prompt lists all 25.

```
Occupation:
Practical nurse (series 0620) | posted as: Licensed Practical Nurse; Practical Nurse (Outpatient); Practical Nurse (Family Medicine); Licensed Practical/Vocational Nurse | You will care for patients with practices that do not require a professional nurse education. In this field, you should have a practical or vocational nursing license from your state, territory or Washington, D.C. | 100% want a licence/certification | ~1914 permanent entry hires/yr
  What real announcements say about this work:
   - Licensed Practical Nurse - Patient Aligned Care Team: This position is eligible for the Education Debt Reduction Program (EDRP), a student loan payment reimbursement program. You must meet specific individual eligibility requirements in accordance with VHA policy and submit your EDRP application within four months of appointment. Program Approval, award amount (up to $200,000) and eligibility period (one to five years) are determined by the VHA Educa
   - Licensed Vocational Nurse (Community Care) - EDRP Authorized: This position is eligible for the Education Debt Reduction Program (EDRP), a student loan payment reimbursement program. You must meet specific individual eligibility requirements in accordance with VHA policy and submit your EDRP application within four months of appointment. Program Approval, award amount (up to $200,000) and eligibility period (one to five years) are determined by the VHA Educa

Score every statement 0-4. Return one entry per statement id.

0. Spend the day moving people through appointments, admissions, discharges and phone requests in a clinic or ward, keeping schedules and records aligned.
1. Move through forests, brush and remote fire lines during seasonal surges, cutting line, mopping up hotspots and responding when conditions turn dangerous fast. Pass a fitness test.
2. Install, modify and test the control and communication systems that keep power generation or spacecraft hardware running to specification, splitting your time between a desk and a test lab.
3. Plan, deliver, and evaluate programs that help people learn, adjust, or improve their functioning at work, school, or in daily life.
4. Collect, prepare, and handle patient samples, records, or equipment needed for diagnosis and treatment, following established clinical procedures.
5. Keep a radio or phone line open for emergencies, send the right unit to the right place, and update crews as the situation changes, with one urgent call interrupting the next.
6. Assist with protecting public lands and facilities by guiding visitors, explaining rules and resources, and helping respond to hazards such as fire, water, or environmental damage.
7. Inspect food, drugs, toys or household products at plants, warehouses or border points, sampling items and documenting violations before unsafe goods reach the public. What you write up can become a legal case.
8. Provide direct care and support to patients in a clinical setting, helping with examinations, treatments, and basic comfort needs.
...
```

### Rating — response

```json
{"ratings": [
  {"question_id": 0, "score": 0}, {"question_id": 1, "score": 0}, {"question_id": 2, "score": 0},
  {"question_id": 3, "score": 0}, {"question_id": 4, "score": 1}, {"question_id": 5, "score": 0},
  {"question_id": 6, "score": 0}, {"question_id": 7, "score": 0}, {"question_id": 8, "score": 4},
  {"question_id": 9, "score": 0}, {"question_id": 10, "score": 0}, {"question_id": 11, "score": 0},
  {"question_id": 12, "score": 0}, {"question_id": 13, "score": 0}, {"question_id": 14, "score": 0},
  {"question_id": 15, "score": 0}, {"question_id": 16, "score": 3}, {"question_id": 17, "score": 0},
  {"question_id": 18, "score": 0}, {"question_id": 19, "score": 0}, {"question_id": 20, "score": 0},
  {"question_id": 21, "score": 0}, {"question_id": 22, "score": 0}, {"question_id": 23, "score": 0},
  {"question_id": 24, "score": 1}
]}
```

That array, in order, is the `profile` shipped for 0620 in the s4 section above.
Missing cells are filled with the item's column mean; more than 25% missing
raises rather than averaging a hole.

---

## The objective

`pipeline/s5_questions.py`. `P` is occupations × items.

```python
def _score_instrument(P, hires, names, cfg):
    w  = hires / hires.sum()
    wm = (w[:, None] * P).sum(0)
    wv = (w[:, None] * (P - wm) ** 2).sum(0)      # hiring-weighted variance per item
    sd = P.std(1, keepdims=True); sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    S  = Pz @ Pz.T / P.shape[1]                   # occupation x occupation similarity
    top = np.argsort(-hires)[:30]                 # the 30 biggest entry-level hirers
    sub = S[np.ix_(top, top)]
    twins = [(names[top[a]], names[top[b]], round(float(sub[a, b]), 3))
             for a in range(len(top)) for b in range(a + 1, len(top))
             if sub[a, b] >= cfg["scoring"]["report_twin_threshold"]]   # 0.80
    return {"hiring_weighted_var": wv,
            "mean_similarity_top30": float(sub[~np.eye(len(top), dtype=bool)].mean()),
            "unresolvable_twins": twins}
```

```python
def _objective(P, hires, names, cfg, baseline_coverage=None):
    m = _score_instrument(P, hires, names, cfg)
    if baseline_coverage is not None:
        cov = _respondent_spread(P)
        if cov < COVERAGE_FLOOR * baseline_coverage:      # 0.95
            return float("inf")
    return m["mean_similarity_top30"] + 0.02 * len(m["unresolvable_twins"])
```

Lower is better. Coverage is a constraint rather than a weighted term:

```python
def _respondent_spread(P, n=3000, seed=0):
    sd = P.std(1, keepdims=True); sd[sd == 0] = 1e-9
    Pz = (P - P.mean(1, keepdims=True)) / sd
    rng = np.random.default_rng(seed)
    R = rng.integers(0, 5, size=(n, P.shape[1])).astype(float)
    z = (R - R.mean(1, keepdims=True)) / (R.std(1, keepdims=True) + 1e-100)
    top1 = np.argmax(z @ Pz.T / P.shape[1], axis=1)
    _, counts = np.unique(top1, return_counts=True)
    p = counts / counts.sum()
    effective = float(np.exp(-(p * np.log(p)).sum()))     # exp(entropy)
    return effective / len(Pz)
```

Pruning: drop items with hiring-weighted variance < `0.35`; then drop one of any
item pair with |r| ≥ `0.80`, keeping the higher-variance one; then restore the
best item from any axis emptied by pruning. `n_samples: 3` independent
generations, best kept. `residual_rounds: 2`, six items each, kept only if the
objective improves.

### Measured

Same 175 occupations, same formula:

| item set | mean similarity, top 30 | distinct #1 of 5,000 | tied pairs |
|---|---|---|---|
| official, 32 items | 0.169 | — | 10 |
| generated, pruned | 0.032 | — | 16 |
| shipped, 25 items (14 narrow + 11 broad) | 0.067 | 232 | 8 |

Pairs the official items cannot separate, from
`data/generated_questions_report.json`:

```
0.978  Customs and border protection            | Criminal investigating
0.943  Border patrol enforcement                | Criminal investigating
0.940  Border patrol enforcement                | Police
0.930  Border patrol enforcement                | Customs and border protection
0.916  Miscellaneous clerk and assistant        | General business and industry
0.893  Human resources assistance               | Human resources management
0.882  Miscellaneous administration and program | General business and industry
0.834  Correctional officer                     | Police
0.812  Contracting                              | Miscellaneous administration and program
0.807  Nursing assistant                        | Practical nurse
```

Item edits re-rate the whole catalogue and are rejected unless the numbers hold
(`instrument/reword.py`):

```python
BAND = {"similarity": 0.090, "cov": 205, "ties": 11}
...
if not (sim <= BAND["similarity"] and cov >= BAND["cov"] and ties <= BAND["ties"]):
    raise SystemExit("reworded set fell outside the band — not promoting")
```

---

## Scoring in the browser

`site/app.js`. No server, no API — `data.json` carries the questions, the 302
profiles, and everything on a card.

```js
function zscore(v) {
  const mean = v.reduce((x, y) => x + y, 0) / v.length;
  const sd = Math.sqrt(v.reduce((x, y) => x + (y - mean) ** 2, 0) / v.length);
  ...
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
```

Answers are 1–5, one digit each, so a run encodes as a query string —
`?a=5235412534251345231453241`. A link whose length does not match the current
question count, or that carries a digit off the scale, is ignored.

---

## What is not tested

The ratings are one model's reading of each occupation, grounded in posting text
rather than job titles. Nothing here measures whether a rating is correct — only
whether the questions produce ratings that tell occupations apart.
