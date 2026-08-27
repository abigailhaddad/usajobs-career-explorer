# Entry-level federal career quiz

A rebuild of the USAJOBS Career Explorer, aimed at entry-level hiring. Answer 25
questions about the kind of work you'd want, get federal occupations ranked by
fit — each one showing how many people it hired at entry level last year, how
many of those jobs are open to the public now, and what you'd need to qualify.

Everything on a card is about entry-level hiring: grades 01–09 on GS-style pay
plans, plus wage-grade trades.

Live at [usajobs-career-explorer.abigailhaddad.com](https://usajobs-career-explorer.abigailhaddad.com).

Not affiliated with USAJOBS or OPM. The official tool is at
[usajobs.gov/careerexplorer](https://www.usajobs.gov/careerexplorer/).

## Run it

```bash
python run.py        # fetch and rebuild everything; writes data/CHANGES.md
python serve.py      # open the quiz at http://localhost:8899
```

Stage 5 rewrites the questions and is opt-in because it calls an LLM:
`python run.py --stages 5`. Responses are cached in `data/.llm_cache`; a full
uncached run is about $0.35.

Two things live outside the repo. Stage 7 reads OPM's qualification standards
from `~/Documents/repos/opm-educ-req/cache` (`STANDARDS_CACHE` in
`pipeline/config.py`), and stage 5 reads an OpenAI key from `env_file` in
`pipeline/questions_config.yaml`.

## The pipeline

```mermaid
flowchart TD
    CE[Career Explorer<br/>page HTML] --> S1
    R2[usajobs_historical<br/>R2 bucket] --> S2
    HF[OPM/EHRI accessions<br/>HuggingFace] --> S3
    STD[OPM qualification<br/>standards] --> S7

    S1[s1 quiz<br/>302 occupations] --> S4
    S2[s2 openings<br/>what's posted, what it asks for] --> S4
    S3[s3 hires<br/>who actually got hired] --> S4
    S7[s7 standards<br/>is a degree required] --> S4

    S4[s4 build<br/>one row per occupation → data.json] --> SITE[site/]

    S2 -.job postings.-> S5[s5 questions<br/>LLM, opt-in]
    S4 -.hiring volume.-> S5
    S5 -.the 25 questions.-> S4
```

A failed stage keeps its previous output and the run exits non-zero, with
`CHANGES.md` naming the stale tables. Otherwise a broken fetch reads as "no
changes" and nothing looks wrong.

## How the scoring works

Everything runs in the browser. The page carries the 25 questions and, for each
of 302 occupations, 25 ratings saying how central each kind of work is to that
job. Your answers become 25 numbers too, and occupations get ranked by how
closely their ratings track your answers.

The 302 occupations come from the official tool. They cover 93% of federal
hiring since 2021, but hardly any Pathways hiring: most student and intern hires
land in occupations the official tool never rated, so this one can't rank them
either.

## What's on a card

**Hires** are OPM/EHRI accessions from 2021 on, counted as permanent hires at
entry grade — permanent meaning tenure groups 1 and 2. Banded pay plans are left
out of that count, because on those a low band number means senior, not junior.

**Openings** come from the `usajobs_historical` R2 bucket, counting only
postings the public, students, or recent graduates can apply to. It counts
openings rather than announcements, because one announcement can carry hundreds
of jobs.

**Whether you need a degree** combines four sources — posting text, OPM's
published standard, what entry hires held, what hires at any grade held — since
none of them is reliable alone. That gives 88 occupations needing a degree, 209
not, 4 needing a credential, 1 unknown. The credential category exists for
practical nurses: only 9% hold a bachelor's, so the data says no degree needed,
but you can't do the job without an LPN diploma.

## Writing the questions

The questions only help if occupations are rated differently on them. Two jobs
rated nearly the same across all 25 questions get nearly the same match score no
matter what you answer, so they always come up together and the quiz can't tell
you which one suits you better.

The official questions leave a lot of jobs in that state. Across the 175
occupations doing most of the entry-level hiring, those 32 questions put the
biggest hirers at 0.17 average similarity to each other, and ten pairs come out
interchangeable:

| pair | similarity |
|---|---|
| Criminal investigating / Customs and border protection | 0.98 |
| Correctional officer / Police | 0.83 |
| Nursing assistant / Practical nurse | 0.81 |

Nursing assistant and practical nurse hire about the same number of people, and
one of them needs an LPN diploma. Someone deciding between the two would want the
quiz to tell them apart, and it can't.

So stage 5 writes its own questions, and scores them the same way: rate every
occupation 0 to 4 on every question, then compare the 30 biggest hirers to each
other and average how alike they come out. A lower number means the questions
pull jobs apart. On the same occupations, that goes from 0.17 to 0.03.

That number can improve while the quiz gets worse. A 6-question version scored
better than the 21-question one, because fewer questions give occupations less
to differ on. It also gave thousands of simulated quiz-takers only 32 different
top matches between them — it separated the occupations without giving different
people different results.

So variety is checked separately, as pass or fail: simulate 3,000 takers, count
how many occupations turn up as someone's top match, and reject any question set
that loses more than 5% of that. This started out as part of the score, weighted
against separation, and the scoring kept preferring question sets that gave up
variety to get a better similarity number.

The rest is pruning and retries. Drop questions that nearly every occupation
gets the same rating on, and drop one of any two questions that measure the same
thing. Generate three separate sets and keep whichever scores best, since asking
the model for questions gives different results each time, while re-rating the
same questions is stable. Then send the pairs that are still tied back to the
model, asking for questions that would split those specific pairs, and keep that
round only if the score improves.

The site ends up with 25 questions, 14 specific and 11 broad. The specific ones
separate the big hirers well, but three of twelve kinds of work had no question
that spoke to them at all; adding broad questions brings that down to one. Broad
questions push the similarity number back up, because more occupations get
similar ratings on them. Everything else improved: ties among the big hirers
fell from around 20 to around 7, and the number of occupations that can come up
as someone's top match went from roughly 150 to 210.

`instrument/` holds the scripts behind that final set. `s4_build` uses
`mixed_questions.parquet` if it's there, falls back to stage 5's own output, and
falls back again to the official 32 questions.
