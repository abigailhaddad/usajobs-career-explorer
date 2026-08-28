# Methodology

Every prompt, sample and number below is read from the pipeline at build time,
so this document describes the run that produced the current site and not an
earlier one. One occupation, **Practical nurse (series 0620)**, runs
through all of it.

Rebuild everything with `python run.py --full`.

---

## The pipeline

```
python run.py --full     # stages 1,2,3,7,4,5,4

  s1 quiz        the official catalogue: 302 occupations and its own questions
  s2 openings    what is posted, who may apply, and what the work is
  s3 hires       who actually got hired, from OPM personnel records
  s7 standards   whether a degree is legally required
  s4 build       one row per occupation -> site/data.json
  s5 instrument  families -> narrow items -> broad items -> combine -> audit
  s4 build       again, now that the questions exist
```

Stage 4 appears twice because the dependency runs both ways: stage 5 rates
occupations from `series_facts`, which stage 4 builds, and stage 4 writes the
payload from the questions, which stage 5 builds. Running stage 5 against a
stale `series_facts` once rated 86 of 302 occupations against job titles the
site no longer showed, and every stage still reported success, so stage 5 checks
before it starts:

```python
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
```

---

## What the model is shown

Both the generation and rating prompts describe an occupation through
`_occ_blurb`, and nothing else about it reaches the model. The posting text
comes from stage 2:

```python
# MajorDuties, not JobSummary. JobSummary is where agencies put recruitment
# boilerplate: every VA practical nurse posting led with the student loan
# repayment programme and said nothing about nursing, so the model was
# rating the occupation on a benefits blurb.
# MajorDuties is an array of strings. The [*] form unescapes each element;
# casting the whole array to VARCHAR[] instead leaves JSON quotes embedded
# in the text, which is what the model then read.
duties_expr = (f"array_to_string(json_extract_string({D}, "
               f"'$.UserArea.Details.MajorDuties[*]'), ' ')")
# One candidate per hiring agency before ranking. Without this the pool is
# whoever writes longest: 0620 has DoD, Bureau of Prisons and DOJ postings,
# but VA runs 954 of its 1,186 announcements and filled every slot.
cands = con.execute(f"""
SELECT series, title, agency, duties FROM (
  SELECT *, row_number() OVER (PARTITION BY series ORDER BY n DESC) AS rk
  FROM (
    SELECT *, row_number() OVER (PARTITION BY series, agency ORDER BY n DESC) AS rk_agency
    FROM (
      SELECT {series} AS series, positionTitle AS title,
             hiringAgencyName AS agency,
             substr({duties_expr}, 1, 900) AS duties,
             length({duties_expr}) AS n
      FROM read_parquet([{urls}]), json_each(JobCategories) AS s
      WHERE {reach} AND length({duties_expr}) >= 200
    )
  ) WHERE rk_agency = 1
) WHERE rk <= {TEXT_CANDIDATES}""").df()
```

`MajorDuties` matters. The field used before was `JobSummary`, which is where
agencies put recruitment copy: every Practical nurse posting led with the
student loan repayment programme and said nothing about nursing, so the model
was rating the occupation on a benefits blurb.

Which postings get kept is decided by how much they differ from each other:

```python
def pick_varied(g, keep=TEXT_KEEP):
    """Keep the longest posting, then the ones least like what is already kept.

    Ranking by length alone returned three copies of the same VA announcement.
    Deduping by hiring agency is not enough either: two agencies often run the
    same boilerplate. So compare the text itself and take the spread.
    """
    rows = g.to_dict("records")
    if len(rows) <= keep:
        return rows
    bags = [_words(r["duties"]) for r in rows]
    chosen = [0]                                   # rows arrive longest-first
    while len(chosen) < keep:
        nxt = min((i for i in range(len(rows)) if i not in chosen),
                  key=lambda i: (max(_overlap(bags[i], bags[j]) for j in chosen), i))
        chosen.append(nxt)
    return [rows[i] for i in chosen]
```

For Practical nurse that returns 3 postings:

```
[Veterans Health Administration] Practical Nurse
[Defense Health Agency] Practical Nurse
[Department of Defense] Practical Nurse (Outpatient)
```

---

## s5b — writing the questions

Generating with `gpt-5.4-mini`, rating with `gpt-5.4-nano`, temperature
0.0, scale 0–4.
6 independent instruments are drawn and the best kept, because
generation swings run to run while re-rating the same items does not.

### Generation — system prompt

```
You write items for a career-matching instrument for federal jobs. Each item is one kind of work, and a respondent rates how much they would want to do it. The instrument is only useful when occupations are rated differently from each other: if two jobs score alike on every item, no answer a person gives can tell them apart. Write items that split the occupations you are shown.

Draw items from these axes:
- Who you deal with all day: patients, inmates, taxpayers, travellers, soldiers, applicants, scientists, no one
- Where the work physically happens: hospital ward, forest, border, warehouse, courtroom, cubicle, aircraft hangar
- What passing through the door requires: a specific degree, a licence, a clearance, a fitness test, an age limit, nothing
- Rhythm and stakes: same task repeated to a standard, one long case at a time, emergencies, seasonal surges
- What a wrong call costs: money, a legal outcome, someone's health, someone's safety, nothing much

Write each item as a plain description of the activity, in the imperative, the way a job description would put it — e.g. "Explain rules, benefits and next steps to people worried about their own case, with every conversation different." Two sentences at most. Never begin with "Would you rather", "Do you like", "Are you interested in", or any question form. No question marks.


Do not write generic interest items ("working with data"). Do not write two items that would score the same across these occupations. Do not ask about skills or qualifications the person already has — describe the work itself, so a 17-year-old can react to it. Every item must be one a real federal job would answer differently from most others on the list.
Do not end an item by restating the axis you drew it from. Clauses like "where the wrong call can affect safety", "the main cost of a mistake is lost time or money", "with the same tasks repeated to a standard" or "in a rhythm where one urgent call interrupts the next" read as machine-written and get stripped by hand every time they appear. Describe the work and stop.
```

### Rating — system prompt

```
You are rating one federal occupation against a list of statements about work. Use the real posting titles and qualification facts supplied, not your general impression of the job title. Score how central each statement is to the everyday work of someone hired into this occupation at entry level.
```

### Rating — one real call, in full

Cache key `8cee1ad9808b3dd9e6b43e2a0f113912`. The prompt is rebuilt from the code and hashed the way
`pipeline/llm.py` hashes it, so this is the call that produced the response
below, not a reconstruction of one like it.

```
Occupation:
Practical nurse (series 0620) | posted as: Licensed Practical Nurse; Practical Nurse (Outpatient); Practical Nurse (Family Medicine); Licensed Practical/Vocational Nurse | You will care for patients with practices that do not require a professional nurse education. In this field, you should have a practical or vocational nursing license from your state, territory or Washington, D.C. | 100% want a licence/certification | ~1914 permanent entry hires/yr
  What real announcements say the work is:
   - Practical Nurse (Veterans Health Administration): VA Careers - Licensed Practical Nurse: https://youtube.com/embed/Ae85IP1Oiz4 Total Rewards of a Allied Health Professional ROLE RESPONSIBILITIES AND ACCOUNTABILITIES: This is an advanced level PACT LPN position. You will monitor and capture workload credit, develop reporting procedures and participate in performance improvement activities aimed at improving patient care access and PACT team processes. For all assignments above the full performance level, the higher-level duties must consist of s
   - Practical Nurse (Defense Health Agency): Review patient medical history. Perform or assist in the performance of a number of specialized medical and minor surgical procedures. Administer medications by oral, topical, intradermal, subcutaneous, and intramuscular routes. Recognize the nature of emergencies and provide first aid in accordance with emergency protocols. Reinforce and reiterate instructions previously presented by the physician or nurse.
   - Practical Nurse (Outpatient) (Department of Defense): Provides practical nursing care to patients in the outpatient clinic with a variety of medical conditions. Performing tasks such as recording vital signs, ordering labs, tracking patient’s laboratory, radiology orders and referral results to completion, and assisting in making future appointments. Initiates and maintains medical histories on patients, records observations, and identifies symptoms for use by the clinician. Medication Administration and Observation. Administers prescribed medicati

Score every statement 0-4. Return one entry per statement id.

0. Speak with beneficiaries about their claims, gather facts and evidence, and make decisions that determine whether benefits are paid and how much. Explain rights, rules and next steps to people whose case is still open.
1. Handle one long claim at a time, gathering evidence, interviewing people, and writing up a decision that will stand up to review.
2. Walk a correctional unit, direct inmate movement, and respond when a fight, escape attempt, or other security problem starts to unfold.
3. Review invoices, vouchers and account records to verify that charges are proper before money is paid or collected. Reconcile figures, correct errors and keep the financial paperwork moving for an office or program.
4. Work through a stack of purchase requests, compare vendors, negotiate terms, and award contracts that keep an agency supplied without wasting money.
5. Check travelers and cargo at a port of entry, deciding who and what can come through after a brief inspection and interview.
6. Inspect vehicles, badges and cargo at a guarded gate, deciding who gets through and who is turned away. Stay alert for trespass, theft or trouble while making rounds around federal property.
7. Review incoming requests line by line, identify exactly what records must be searched or released, and route each case through the next step.
8. Track a stack of budget transactions, reconcile figures against funding targets, and flag anything that would change the status of funds.
9. Review applications, interview people, and weigh evidence before deciding whether a person meets the standard for protection or relief in a case that can change their legal status.
10. Clean and treat patients' teeth, work around medical complications and anxiety, and adjust care to what the person in the chair can tolerate that day.
11. Inspect aircraft wiring, sensors, and integrated electronic boxes on the flight line, then troubleshoot failures and replace components until the system passes operational checks.
12. Move parts, tools, and supplies to the exact work area before mechanics stall, checking incoming shipments and shifting priorities as repair jobs change.
13. Compare spending plans, obligations, and reimbursements against the rules, then recommend whether a charge should be accepted or denied.
14. Spend the day in a hospital ward or clinic moving patients through appointments, answering their questions, and keeping their records and referrals moving.
15. Track and reconcile shelves, bins, and issue records for parts and supplies that must match the count on the floor, then chase down every mismatch until the paperwork and the stock agree.
16. Issue, account for, and inventory weapons and ammunition for security personnel, keeping every item traced and secured.
17. Track complaints, allegations, and supporting records through an investigation, sort out which office should handle each matter, and build a case file from interviews and documents.
18. Inspect work areas, products, or samples for cleanliness, quality, safety, or compliance with standards.
19. Organize, maintain, and retrieve records, files, and reference materials so information can be found, used, and preserved correctly.
20. Inspect technical systems and equipment, diagnose problems, and plan or oversee maintenance, repair, installation, or modification work.
21. Monitor natural areas, facilities, and public use to protect people, property, and environmental resources. Report hazards, enforce basic rules, and take action to prevent damage, theft, fire, or unsafe conditions.
22. Prepare, review, and edit written or visual information for official use, public communication, or archival and administrative purposes.
23. Provide guidance, supervision, or support services to people in an institutional, educational, or community setting. Document needs, progress, and outcomes, and coordinate with other staff to help carry out programs or services.
24. Review applications, records, statements, and other evidence to determine whether people or documents meet legal or regulatory requirements.
```

### Rating — the response

```json
{"ratings": [
  {"question_id": 0, "score": 0}, {"question_id": 1, "score": 0}, {"question_id": 2, "score": 0},
  {"question_id": 3, "score": 0}, {"question_id": 4, "score": 0}, {"question_id": 5, "score": 0},
  {"question_id": 6, "score": 0}, {"question_id": 7, "score": 0}, {"question_id": 8, "score": 0},
  {"question_id": 9, "score": 0}, {"question_id": 10, "score": 4}, {"question_id": 11, "score": 0},
  {"question_id": 12, "score": 0}, {"question_id": 13, "score": 0}, {"question_id": 14, "score": 3},
  {"question_id": 15, "score": 0}, {"question_id": 16, "score": 0}, {"question_id": 17, "score": 0},
  {"question_id": 18, "score": 1}, {"question_id": 19, "score": 2}, {"question_id": 20, "score": 0},
  {"question_id": 21, "score": 0}, {"question_id": 22, "score": 0}, {"question_id": 23, "score": 1},
  {"question_id": 24, "score": 0}
]}
```

That array is the profile shipped for series 0620:

```json
[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0, 0, 3, 0, 0, 0, 1, 2, 0, 0, 0, 1, 0]
```

---

## s5d — choosing the instrument

Narrow items separate particular occupations; broad items cover kinds of work
the narrow ones miss. The split between them is not assumed. Every split that
clears the reach floor is rated for real and compared on what it does, because
profiles rated in separate calls are not good enough to choose between them —
they predicted 0.140 similarity for a split that measured 0.066.

```python
# The proxy only decides which splits are worth measuring. It is not good
# enough to choose between them: profiles rated in separate calls predicted
# similarity 0.140 for 17 + 8 that measured 0.066, and 2 collapses for
# 13 + 12 that measured 4. So every split clearing the reach floor is rated
# for real and compared on what it actually does. Responses are cached, so
# this is paid once.
eligible = [t for t in trials if t["cov"] >= REACH_FLOOR] or trials
print(f"\n  {len(eligible)} of {len(trials)} splits clear the proxy reach floor "
      f"of {REACH_FLOOR}; rating each of them for real")
```

### Why not ties, and why not similarity

A tie is not a defect on its own. Contact representative and
Veterans claims examining really do resemble each other, and a quiz
that claimed to separate them on interest alone would be lying. So each tied
pair is checked against the two occupations' posting text:

```python
def _vectors(docs):
    """TF-IDF, normalised, one vector per occupation."""
    n = len(docs)
    df = Counter()
    for t in docs.values():
        df.update(set(t))
    idf = {w: math.log(n / c) for w, c in df.items()}
    out = {}
    for s, t in docs.items():
        v = {w: (1 + math.log(c)) * idf.get(w, 0.0) for w, c in Counter(t).items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out[s] = {w: x / norm for w, x in v.items()}
    return out
```

Selecting on similarity instead chose a set with 8 collapses; selecting on reach
chose 0.148 similarity. Selecting on collapses chose this one.

---

## What the site ships

```
25 questions: 18 narrow, 7 broad
similarity among the 30 biggest hirers : 0.108
tied pairs                             : 10
occupations reachable as a top match   : 263 of 302
```

The official 32 questions, scored the same way on the same occupations, come out
at 0.169 with 10 tied pairs.

Every rating call behind the live site is browsable at
[/appendix](https://usajobs-career-explorer.abigailhaddad.com/appendix): the
postings each occupation was described by, the exact prompt, and the scores
returned, with each pairing verified by hash.

---

## The other stages

```
s1  data/questions.parquet        32 official questions
    data/series_profiles.parquet   302 occupations, 32 floats each
s2  data/openings.parquet          605 rows x 26 cols
    data/series_text.parquet       418 occupations with posting text
s3  data/hires.parquet             645 rows x 17 cols
s7  data/opm_standards.parquet     415 rows
s4  site/data.json                 the whole site
```

Definitions that decide what counts as an entry-level job:

```python
PUBLIC_PATHS = ("public", "The public", "student", "Students",
                  "graduates", "Recent graduates")
GRAD_PATHS = ("student", "Students", "graduates", "Recent graduates")
```

```python
PERM = "(tenure LIKE 'TENURE GROUP 1%' OR tenure LIKE 'TENURE GROUP 2%')"
ENTRY = (f"((pay_plan_code IN ({_sql_list(GS_LIKE)}) AND TRY_CAST(grade AS INT) "
         f"BETWEEN 1 AND {ENTRY_MAX_GRADE}) OR pay_plan_code IN ({_sql_list(TRADE_PLANS)}))")
```

Banded pay plans are excluded rather than counted: a low number there means
senior, and 575 Senior Executive Service postings were being read as entry level
because their grades read 01.

---

## What is not tested

The ratings are one model's reading of each occupation, from posting text rather
than job titles. Nothing here measures whether a rating is correct. What is
measured is whether the questions produce ratings that tell occupations apart,
and whether the pairs they cannot tell apart are genuinely alike.
