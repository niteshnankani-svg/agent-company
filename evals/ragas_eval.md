# RAGAS eval run

- **Run at**: 2026-09-30T05:57:30.375371+00:00
- **Judge/LLM**: gemini/gemini-3.8-flash (via Gemini's OpenAI-compatible endpoint, openai.AsyncOpenAI)

**Scope note**: only Legal and HR are included - the two departments with a genuine retrieve-then-generate shape. Legal's contexts are citation summaries (not raw indexed passage text - the API doesn't expose that). HR's contexts are the SQL evidence JSON the agent already returns. See this script's docstring for the full rationale.

- **Avg faithfulness**: 0.000
- **Avg context precision**: 0.750
- **Avg factual correctness**: 0.600

---

## legal-section-357

**Question**: What is Section 357 of BNS about?
**Reference**: Section 357 of the Bharatiya Nyaya Sanhita 2023 covers breach of a lawful contract to attend on or supply the wants of a helpless person (someone helpless due to youth, unsoundness of mind, or disease/bodily weakness). Punishment: imprisonment up to three months, or a fine up to five thousand rupees, or both.

**Answer**: # Section 357 of Bharatiya Nyaya Sanhita 2023

Section 357 deals with **Breach of contract to attend on and supply wants of helpless person**.

## Key Elements:

**Who is liable:**
A person who is bound by a lawful contract to attend on or supply the wants of another person.

**Against whom:**
Any person who, by reason of:
- youth, or
- unsoundness of mind, or
- disease or bodily weakness

is helpless or incapable of providing for their own safety or supplying their own wants.

**The offense:**
Voluntarily omitting to do so (i.e., failing to fulfill the contractual obligation).

**Punishment:*

**Retrieved contexts** (5):
- Section 357 (Breach of contract to attend on and supply wants of helpless person): This section prescribes punishment for breaching a contract to care for or supply the needs of a helpless person.
- Section 37 (Acts against which there is no right of private defence): This section lists acts against which there is no right of private defence.
- Section 10 (Punishment of person guilty of one of several offences, judgment stating that it is doubtful of which): This section addresses punishment when it's unclear which of several offences was committed.
- Section 49 (Punishment of abetment if act abetted is committed in consequence): Details the punishment for abetment when the act abetted is committed.
- Section 160 (Abetment of mutiny, if mutiny is committed in consequence thereof): This section penalizes abetment of mutiny if the mutiny is committed as a consequence.

- **Faithfulness**: 0.000
- **Context precision**: 1.000
- **Factual correctness**: 0.800

---

## legal-section-37

**Question**: What does Section 37 of BNS say about right of private defence?
**Reference**: Section 37 of the Bharatiya Nyaya Sanhita 2023 lists the acts against which there is no right of private defence.

**Answer**: # Section 37 of BNS 2023: Right of Private Defence - Overview

## Key Provisions:

**Section 37** establishes **limitations on the right of private defence** in two main parts:

### Part (1) - Acts Against Which There is No Right of Private Defence:

There is **no right of private defence** against:

**(a)** Acts by public servants acting in good faith under colour of office that do not reasonably cause apprehension of death or grievous hurt, even if not strictly justifiable by law [BNS, Section 37(1)(a)]

**(b)** Acts done by direction of a public servant acting in good faith under colour of 

**Retrieved contexts** (3):
- Section 357 (Breach of contract to attend on and supply wants of helpless person): This section prescribes punishment for breaching a contract to care for or supply the needs of a helpless person.
- Section 37 (Acts against which there is no right of private defence): This section lists acts against which there is no right of private defence.
- Section 35 (Right of private defence of body and of property): This section establishes the right of private defence of body and property.

- **Faithfulness**: 0.000
- **Context precision**: 0.500
- **Factual correctness**: 0.400

---

## hr-sales-headcount

**Question**: How many employees are in the Sales department?
**Reference**: There are 1035 employees in the Sales department, per Employee_Performance_Dataset.csv.

**Answer**: According to the retrieved records, there is no employee in the Legal department. The SQL result indicates that the count of employees in the Legal department is 0.

**Retrieved contexts** (0):

*missing contexts or answer - likely escalated, skipping metrics*

---

## hr-it-headcount

**Question**: How many employees are in the IT department?
**Reference**: There are 974 employees in the IT department, per Employee_Performance_Dataset.csv.

**Answer**: According to the retrieved records, there is no employee in the Legal department. The SQL result indicates that the count of employees in the Legal department is 0.

**Retrieved contexts** (0):

*missing contexts or answer - likely escalated, skipping metrics*

---

## Findings

**Both HR cases returned the identical wrong answer** ("there is no employee in the Legal
department... count is 0") regardless of whether the question asked about Sales or IT. Neither
scored on RAGAS metrics because there was no answer/context pair to evaluate (both hit the
skip path) - but the failure itself is more interesting than a metrics gap would have been.

This is a live catch of the fuzzy-cache-collision bug already flagged in
[evals.md](evals.md#findings): `DepartmentCache` (`../cache.py`) uses `SequenceMatcher` at a
0.92 similarity threshold. `evals/cases.py` includes a case that asks "How many employees are
in the **Legal** department?" (`hr-question-nonexistent-department`) - textually very close to
this script's "How many employees are in the **Sales** department?" and "...the **IT**
department?". Once the Legal-department query was cached (from an earlier `run_evals.py` run
against the same live backend), both semantically-different Sales/IT questions matched it above
threshold and were served the stale, wrong cached answer instead of hitting the HR agent again.

Net effect: this run independently confirms the cache-collision bug is not just a theoretical
risk from code review - it actively corrupted results in a real eval run, on the first two
queries that happened to be textually similar to something already cached. Not fixed here (out
of scope for this eval task), same as the original finding.
