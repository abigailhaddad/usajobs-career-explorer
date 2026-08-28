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
             substr({duties_expr}, 1, 6000) AS duties,
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
    bags = [_words(r["duties"]) for r in rows]

    # Drop near-identical postings first. Returning early when there are only as
    # many candidates as slots shipped correctional officer with the same
    # Interior announcement twice, at an overlap of 1.000, because the early
    # return skipped the comparison entirely.
    unique, ubags = [], []
    for r, b in zip(rows, bags):
        if all(_overlap(b, ub) < 0.9 for ub in ubags):
            unique.append(r)
            ubags.append(b)
    rows, bags = unique, ubags
    if len(rows) <= keep:
        return rows
    chosen = [0]                                   # rows arrive longest-first
    while len(chosen) < keep:
        nxt = min((i for i in range(len(rows)) if i not in chosen),
                  key=lambda i: (max(_overlap(bags[i], bags[j]) for j in chosen), i))
        chosen.append(nxt)
    return [rows[i] for i in chosen]
```

For Practical nurse that returns 5 postings:

```
[Veterans Health Administration] Practical Nurse
[Defense Health Agency] Practical Nurse
[Other Agencies and Independent Organizations] Licensed Practical Nurse
[Department of Defense] Practical Nurse (Outpatient)
[Department of Justice] Practical Nurse (Dialysis LPN)
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

Cache key `1096433b455b02b49a267a06149905da`. The prompt is rebuilt from the code and hashed the way
`pipeline/llm.py` hashes it, so this is the call that produced the response
below, not a reconstruction of one like it.

```
Occupation:
Practical nurse (series 0620) | posted as: Licensed Practical Nurse; Practical Nurse (Outpatient); Practical Nurse (Family Medicine); Licensed Practical/Vocational Nurse | You will care for patients with practices that do not require a professional nurse education. In this field, you should have a practical or vocational nursing license from your state, territory or Washington, D.C. | 100% want a licence/certification | ~1914 permanent entry hires/yr
  What real announcements say the work is:
   - Practical Nurse (Veterans Health Administration): VA Careers - Licensed Practical Nurse: https://youtube.com/embed/Ae85IP1Oiz4 Total Rewards of a Allied Health Professional ROLE RESPONSIBILITIES AND ACCOUNTABILITIES: This is an advanced level PACT LPN position. You will monitor and capture workload credit, develop reporting procedures and participate in performance improvement activities aimed at improving patient care access and PACT team processes. For all assignments above the full performance level, the higher-level duties must consist of significant scope, complexity (difficulty) and range of variety and be performed by the incumbent at least 25% of the time. Assignments associated with this position include, but are not limited to: Manages patient care problems and deviations using self-direction in accordance with VA Nursing Practice and LPN licensure standards. Maintains knowledge and skill to care for a wide range of patients, including those with chronic and acute illness. Demonstrates comprehensive understanding of disease conditions, nursing principles, and standards/practices. Assesses, records, and reports vital signs, neurological signs, weight, and blood glucose levels. Appropriately triages and prioritizes patient needs as routine, urgent, or emergent in the clinic setting and modifies patient care/treatment as needed. Checks patients into the clinic, interviews patient and/or significant other, and documents intake data as per policy. Possesses comprehensive knowledge of VA protocols and VHA directives related to clinic management, documentation, and computer technology. Demonstrates the ability to independently adjust or modify care being delivered. Responsibilities include prompt recognition and treatment of urgent/emergent patient care needs. Provides high quality nursing care using traditional and non-traditional modalities. Applies knowledge by considering the interrelationship between age, physical, emotional, social, cultural, and population-specific needs when providing care. Serves as a resource for complex/specialized procedures. Performs venipuncture for blood draws, prepares specimens for transport and processing, transcribes orders, accessions label, provides patient education about lab tests, etc. Demonstrates the knowledge and skill to interpret common test results and other medical data. Provides education to patients/families about preparation for procedures and/or diagnostic tests. Demonstrates knowledge and skill to incorporate patient education into care and modifies. Contributes to the interdisciplinary PACT team in development, implementation, and evaluation of the treatment plan. Communicates calmly, courteously, and professionally with all patients, personnel, and visitors while maintaining confidentiality in accordance with HIPAA regulations and Medical Center policies. Uses advanced skills to communicate with verbally or physically abusive patients or visitors using Prevention and Management of Disruptive Behavior (PMDB) principles and policy guidelines. Collaborates with the PACT team and other interdisciplinary services in the development, implementation, and evaluation of patient care. Duties include following up on abnormal results with the provider and educating patients/families on disease management and anticipated plan of care. Work Schedule: Monday - Friday, 8:00 am - 4:30 pm Recruitment Incentive (Sign-on Bonus): Not Authorized. Permanent Change of Station (Relocation Assistance): Not Authorized. EDRP Authorized: Former EDRP participants ineligible to apply for incentive. Contact VISN7EDRPFacilityCoordinators2@va.gov, the EDRP Coordinator for questions/assistance. Learn more Pay: Competitive salary and regular salary increases When setting pay, a higher step rate of the appropriate grade may be determined after consideration of higher or unique qualifications or special needs of the VA (Above Minimum Rate of the Grade). Paid Time Off: 37-50 days of annual paid time offer per year (13-26 days of annual leave, 13 days of sick leave, 11 paid Federal holidays per year) Selected applicants may qualify for credit toward annual leave accrual, based on prior [work experience] or military service experience. Parental Leave: After 12 months of employment, up to 12 weeks of paid parental leave in connection with the birth, adoption, or foster care placement of a child. Child Care Subsidy: After 60 days of employment, full time employees with a total family income below $144,000 may be eligible for a childcare subsidy up to 25% of total eligible childcare costs for eligible children up to the monthly maximum of $416.66. Retirement: Traditional federal pension (5 years vesting) and federal 401K with up to 5% in contributions by VA Insurance: Federal health/vision/dental/term life/long-term care (many federal insurance programs can be carried into retirement) Telework: Not Authorized. Virtual: This is not a virtual position. Functional Statement #: 534-58338F Permanent Change of Station (PCS): Not Authorized.
   - Practical Nurse (Defense Health Agency): Review patient medical history. Perform or assist in the performance of a number of specialized medical and minor surgical procedures. Administer medications by oral, topical, intradermal, subcutaneous, and intramuscular routes. Recognize the nature of emergencies and provide first aid in accordance with emergency protocols. Reinforce and reiterate instructions previously presented by the physician or nurse.
   - Licensed Practical Nurse (Other Agencies and Independent Organizations): As a Licensed Practical Nurse, you will: Perform nursing assignments of a highly specialized nature to include medication administration and hands on nursing care. Identify Resident problems and provides input to the plan of care. Communicate responses and/or changes to others to include all personnel, physicians, and members of interdisciplinary teams. Describe the Resident in writing if indicated to include all the following: physical appearance, emotional status, Resident's interaction with staff and/or family, Resident's knowledge of disease process and Resident's verbal/nonverbal actions/responses. Candidates should be committed to improving the efficiency of the Federal government, passionate about the ideals of our American republic, and committed to upholding the rule of law and the United States Constitution.
   - Practical Nurse (Outpatient) (Department of Defense): Provides practical nursing care to patients in the outpatient clinic with a variety of medical conditions. Performing tasks such as recording vital signs, ordering labs, tracking patient’s laboratory, radiology orders and referral results to completion, and assisting in making future appointments. Initiates and maintains medical histories on patients, records observations, and identifies symptoms for use by the clinician. Medication Administration and Observation. Administers prescribed medications with increased independence, ensuring proper patient dosages, routes, and times are followed. Educates patients and families about prescribed treatments, reinforcing adherence to the medical plan. Documents accurate administered medications and patient responses promptly in compliance with facility policies. Support for Medical Procedures and Emergency Response. Utilizes electronic health record and information technology as effective communication and management tools to facilitate the patient experience and coordination. Collaborates with the healthcare team to develop and adjust care plans based on patient progress and feedback. Participates in team briefings and debriefings to foster a collaborative approach to patient care. Assists in performing and managing medical procedures, physical examinations, and treatments. Ensures administrative requirements related to patient’s visit are completed timely and correctly.
   - Practical Nurse (Dialysis LPN) (Department of Justice): Responsible for functioning within the scope of practice dictated by the authority of the Nurse Practice Act and Bureau of Prisons. Maintaining a current LPN license is a requirement of this position. Provides a full range of patient care to chronically ill dialysis inmates, under the supervision of the Supervisory Clinical Nurse, with guidance from the Charge Nurse. Prepares patients for specialized diagnostic and therapeutic procedures according to standard protocols. Maintains records of medications given. Reports and records adverse reactions through approved reporting mechanisms. Transcribes and carries out the wide variety of physician's orders according to established nursing policies pertaining to orders. Along with all other correctional institution employees, incumbent is charged with responsibility for maintaining security of the institution. The staff correctional responsibilities precede all others required by this position and are performed on a regular and recurring basis.

Score every statement 0-4. Return one entry per statement id.

0. Review benefit claims one case at a time, interview the person about their situation, gather evidence, and decide whether the claim is allowed or denied. Explain the decision, correct discrepancies, and update the record so the payment amount is right.
1. Guide a veteran through a claims interview, gather missing evidence, and decide what the file needs before an award letter can go out.
2. Stand at a clinic front desk and keep the appointment line moving while greeting patients, checking them in, and fixing routine scheduling problems as they come up.
3. Prepare written explanations and status updates for people asking why a case is delayed, what records are being searched, and what comes next.
4. Track detainee identity, prepare removal paperwork, and keep custody records straight while coordinating transport and release steps.
5. Review a request for records, identify what documents must be gathered, and coordinate with other offices to assemble a complete response.
6. Inspect cargo, travelers, and vehicles at a port of entry, decide who and what can enter, and stop prohibited goods before they cross the line.
7. Receive emergency calls, sort out what is happening, and send the right responders while the situation is still unfolding. Keep radio traffic, computer logs, and unit status moving in a center where every minute can change the outcome.
8. Move through a warehouse or storage area receiving shipments, checking counts, shelving items, and pulling supplies for issue or delivery. Use forklifts, pallet jacks, or hand trucks to keep stock organized and ready for the next request.
9. Track a stack of budget transactions, reconcile commitments and obligations, and flag when spending plans need to be adjusted before funds run short.
10. Stand at a hospital entrance or ward post and screen patients, visitors, and staff before they reach restricted areas, checking bags, badges, and weapons as part of the shift. Handle the first response when an alarm, disturbance, or suspicious person needs to be stopped, questioned, or detained.
11. Prepare patients for diagnostic testing, place electrodes or probes, monitor vital signs and waveforms, and stop the procedure if the patient shows distress or a dangerous change. Explain the test, watch for emergencies, and report significant findings immediately to clinical staff.
12. Track aircraft or vehicle maintenance jobs from release to completion, reshuffle priorities when urgent repairs appear, and keep the work order board current.
13. Install, troubleshoot, and repair wiring, controls, fire alarms, and power distribution systems in occupied buildings, working from blueprints and test instruments in cramped spaces and at height. Trace faults, replace damaged components, and restore service without interrupting the facility longer than necessary.
14. Receive, track, store, issue, and move supplies, equipment, and other materials so the right items are available when needed.
15. Gather, interpret, and present information about organizations, markets, policies, or events for decision makers and the public. Coordinate with others to produce reports, briefings, translations, or media materials that support communication and outreach.
16. Interview people, gather facts, and prepare reports or recommendations based on findings from legal, safety, or enforcement work.
17. Analyze technical problems in systems, equipment, or processes and apply engineering or physical science principles to solve them.
18. Review forms, records, and supporting documents for completeness, accuracy, and compliance with established rules and procedures.
19. Clean and sanitize work areas, equipment, and facilities to maintain safe and orderly conditions.
20. Plan, test, inspect, maintain, or improve complex machinery, structures, instruments, or control systems used in government operations.
21. Collect information, observe conditions, and record findings to support treatment, diagnosis, counseling, or supervision. Use standard tools, tests, interviews, or reports to communicate results to licensed professionals or other staff.
22. Prepare and deliver clear information to audiences in writing, in person, or through media channels. Tailor the message to different groups and purposes while keeping it accurate and consistent.
23. Check orders, inventory, and deliveries for accuracy, and keep records or paperwork that support buying, stocking, safeguarding, and distributing government property.
24. Provide administrative support by organizing information, updating files and systems, preparing correspondence or reports, and helping resolve routine customer or employee issues.
```

### Rating — the response

```json
{"ratings": [
  {"question_id": 0, "score": 0}, {"question_id": 1, "score": 0}, {"question_id": 2, "score": 2},
  {"question_id": 3, "score": 0}, {"question_id": 4, "score": 0}, {"question_id": 5, "score": 0},
  {"question_id": 6, "score": 0}, {"question_id": 7, "score": 0}, {"question_id": 8, "score": 0},
  {"question_id": 9, "score": 0}, {"question_id": 10, "score": 0}, {"question_id": 11, "score": 4},
  {"question_id": 12, "score": 0}, {"question_id": 13, "score": 0}, {"question_id": 14, "score": 0},
  {"question_id": 15, "score": 0}, {"question_id": 16, "score": 0}, {"question_id": 17, "score": 0},
  {"question_id": 18, "score": 2}, {"question_id": 19, "score": 1}, {"question_id": 20, "score": 0},
  {"question_id": 21, "score": 4}, {"question_id": 22, "score": 2}, {"question_id": 23, "score": 0},
  {"question_id": 24, "score": 2}
]}
```

That array is the profile shipped for series 0620:

```json
[0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 2, 1, 0, 4, 2, 0, 2]
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
25 questions: 14 narrow, 11 broad
similarity among the 30 biggest hirers : 0.140
tied pairs                             : 7
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
