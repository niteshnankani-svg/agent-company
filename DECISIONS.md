# Decisions

One entry per design choice: what was chosen, what was rejected, why, and what breaks at 100x load.

---

## D-001 — Working branch name

**Chosen:** all work happens on `claude/rebuild-6bbd3f`, and nothing is pushed to `main`.

**Rejected:** a branch literally named `rebuild`.

**Why:** the brief asks for a branch called `rebuild`. This session was provisioned with `claude/rebuild-6bbd3f` as the only branch it may push to, so a branch named `rebuild` could not be pushed. The name carries the same intent. It can be renamed or merged under any name at PR time.

**At 100x load:** not applicable.

---

## D-002 — How Phase 0 evidence was gathered

**Chosen:** read every file; confirm claims with small probe scripts and test runs kept **outside the repo** (session scratchpad), using the dependencies pinned in `requirements.txt` in a throw-away virtualenv.

**Rejected:** (a) editing code or adding test scaffolding inside the repo during Phase 0, which the brief forbids; (b) asserting defects from reading alone when a 10-line probe could settle them; (c) calling live services, which need keys and URLs only the owner has.

**Why:** the owner needs to be able to explain the system, so each claim in `HOW_IT_WORKS.md` is marked as read-from-code or actually run, and the list of things not verified is explicit.

**At 100x load:** not applicable (analysis only).

---

# Phase 1 — one contract for every department

## D-003 — Shape of `DepartmentResult`

**Chosen:** a Pydantic model (`contract.py`) with `department`, `status`, `answer`, `evidence[]`, `confidence`, `missing_fields`, `error`, plus one field I added, `reason`. `confidence` is a nested object `{kind, value}`. `Evidence` is `{source, text?, data?}` and must carry text or data.

**Rejected:** (a) a flat `confidence: float` + `confidence_kind`, because it allows a number with kind `none`; the nested model makes that unrepresentable. (b) A free-form `details: dict` for department-specific extras, because it becomes the place hard-coded labels and numbers hide again. (c) Reusing `error` for "needs a human" reasons, because a human review is not a failure and the UI must tell them apart.

**Why:** `reason` is the machine-readable "why this isn't a plain answer" (for example `security_incident_review`); without it a reviewer or UI has to parse prose. `department` travels with the result so nothing needs the old mutable `active_department` to know who produced it.

**At 100x load:** the model is validated on every result and again at each state boundary (microseconds each, CPU only). Evidence `data` can be large (a full TradeStat record); at 100x, cap evidence size before it is stored in checkpoints (Phase 5).

## D-004 — The status rules are enforced by the model, not by convention

**Chosen:** `answered` requires an answer and evidence; `needs_human` requires a `reason`; `needs_more_info` requires `missing_fields` (and only it may have them); `failed` requires an `error`. Confidence kind `none` must have no value; `calibrated_probability` must be in [0,1].

**Rejected:** documenting the rules and trusting each department to follow them.

**Why:** the Phase 0 audit found five departments with five meanings of confidence. A department that breaks a rule now raises at construction, in a unit test, instead of shipping a plausible-looking wrong result.

**At 100x load:** no change. The cost is a department author sometimes has to decide "this is not really answered", which is the intent.

## D-005 — Graph state holds plain dicts, not model objects

**Chosen:** the state field `department_result` is `result.to_state()` (JSON-safe dict); consumers call `DepartmentResult.from_state()`.

**Rejected:** storing the Pydantic object in state. It works in memory, but Phase 5 adds a checkpointer, and (de)serialising custom classes depends on the serializer's type allow-list. A dict is always safe and still validated at every boundary.

**At 100x load:** each result is serialised once per node transition into the checkpoint store; keep results small (see D-003).

## D-006 — Nodes return partial updates; departments are pure functions

**Chosen:** every node returns only the keys it changes. Lists several nodes add to (`audit_trail`, `department_history`) use an append reducer, so a node returns just the new entries. Each department is a pure `run_*(raw_input, fields, …) -> DepartmentResult`; `departments.py` registers them; `graph.py` builds the five head nodes from one factory.

**Rejected:** (a) keeping `state` mutation and just changing what is written; it breaks checkpoint/resume and hides which node changed what. (b) The old per-department try/except import blocks in `build_graph`, replaced by one registry import.

**Why:** pure runners can be called directly by tests and by the sequential runner. The state-mutation defect (#12 in `HOW_IT_WORKS.md`) is gone: a test asserts every node leaves its input untouched.

**At 100x load:** the departments are stateless, so the API scales horizontally. The in-process routing cache (still present, see D-011) does not.

## D-007 — No invented confidence; only Legal has a number, and it is labelled

**Chosen:** every hard-coded value is removed (finance 0.0, backend 1.0, export 0.85/0.5/0.4, HR 1−p). Legal reports its top retrieval similarity as `retrieval_score`. Everything else is `none`. Jev's complaint probability is kept as **evidence** on the gate result, not as confidence.

**Rejected:** labelling Jev's probability or the ticketing agent's number `calibrated_probability`. Nobody has measured their calibration, and the old docs call the ticketing agent's value a fixed proxy.

**Why:** a number labelled "probability" invites a threshold that means nothing. `retrieval_score` says exactly what it is.

**At 100x load:** n/a. If calibrated numbers are wanted later they need a labelled eval set, not a different field.

## D-008 — Evidence must be material, not labels; three honest limits

**Chosen:** an `answered` result needs real evidence. Where a department cannot supply it, it does not answer:
- **HR:** if the HR agent returns an answer with no `evidence`, the result is `needs_human` (`hr_agent_returned_no_evidence`) with the answer shown to the reviewer. **Unverified assumption:** I could not see the agent's real payload; I rely on its docstring (`{answer, evidence}`). The evidence normaliser accepts a string, a dict or a list.
- **Legal:** the RAG service exposes a title and short summary per section, not the raw statute text, so evidence text is "title: summary".
- **Backend:** the ticketing agent returns a category and an answer, not its sources, so evidence is its classification only. Phase 4 cannot verify claims against sources that do not exist; the verifier will have to treat this department as rule-based.

**Rejected:** inventing evidence (for example using the answer as its own evidence), which would make the Phase 4 verifier check nothing.

**At 100x load:** n/a.

## D-009 — The verifier is deliberately weakened until Phase 4

**Chosen:** interim verifier checks only that a valid `DepartmentResult` exists. Every valid status passes. The old confidence thresholds and the Bedrock "consistency" call are removed.

**Rejected:** (a) porting the thresholds onto the new fields: a threshold on a retrieval score or on `none` proves nothing (defect #1). (b) Keeping the old LLM check: it only ever ran in a narrow confidence band and, with no Bedrock access, always failed.

**What is lost, stated plainly:** between Phase 1 and Phase 4 nothing checks whether an answer is true. It did not really before either (defect #2), but the old code gave the appearance of a check. Do not treat Phase 1 output as verified.

**At 100x load:** the interim verifier costs nothing; Phase 4's LLM check will, and will need a budget.

## D-010 — A `failed` result is not retried; unexpected exceptions become `failed`

**Chosen:** `failed` is terminal (verifier passes it through, no re-run). `run_department` catches any unexpected exception, logs the traceback server-side, and returns `failed` with only the exception type.

**Rejected:** retrying failed departments: that is the blind identical retry (defect #3), and for external services it repeats slow calls. Returning the exception message: it can leak URLs or internals (defect #14).

**At 100x load:** a dependency outage now fails fast instead of tripling the load on a service that is already struggling.

## D-011 — HR answer cache unplugged; routing cache left for Phase 2

**Chosen:** no department caches answers any more, and the verifier no longer writes to a cache. `cache.py` stays only because the orchestrator uses it to remember routing until Phase 2 replaces it.

**Rejected:** porting the fuzzy-match cache onto the new contract. I measured it returning a Sales headcount for a Legal question (similarity 0.936), so porting it would carry a known wrong-answer bug into the new design.

**At 100x load:** no answer cache means every HR question hits the HR agent. That is the safe default until Phase 2 delivers a cache that is keyed correctly and refuses live-data and per-person answers.

## D-012 — Finance: ask for what is missing, name the exact field

**Chosen:** no path or missing fields → `needs_more_info` with the exact field names. Invalid values → the same, naming only the bad fields. Valid AR or AP → `needs_human` with the policy recommendation and the validated input as evidence. To make errors name the right field, the cross-field checks in `finance_policy.py` became field-level validators.

**Rejected:** letting a record-level validation error fall back to "ask for everything"; and making AP an `answered` result. The existing policy is fail-closed (a human approves every payment or credit decision) and I kept it.

**Note for Phase 3:** `is_round_number` and `overdue` are derived values; the user should not be asked for them. Phase 1 still lists them as required.

**At 100x load:** pure local rules; no concern.

## D-013 — Removed the package-relative import fallbacks

**Chosen:** flat imports only (`from contract import …`).

**Rejected:** keeping `try: from x import … except ImportError: from .x import …` in every file. `api.py` already used flat imports and the Dockerfile runs `uvicorn api:app`, so the relative path was untested and unreachable in practice.

**At 100x load:** n/a.

## D-014 — The API response shape changed; the current frontend does not understand it

**Chosen:** `POST /request` returns `{request_id, result: DepartmentResult, needs_human, escalation_reason, department_history, audit_trail}`. `needs_human` is now true whenever `status == "needs_human"`, which also fixes unclassified requests being reported as not needing a human. Audit entries use `status` instead of `decision/source`. `backend_triage` is gone.

**Rejected:** a compatibility layer emitting the old `decision/confidence/rationale/evidence_ids/source` keys. It would re-create the fake single "confidence" and be deleted in Phase 2 anyway.

**Consequence:** the existing Next.js UI reads the old keys and will not render real results until Phase 2 rewrites it (it also silently falls back to its own in-process router today; defect #11).

## D-015 — Test scaffolding: pytest in `tests/`, dummy key as an explicit stopgap

**Chosen:** `tests/` holds new pytest tests (83) with every external service mocked; `pytest.ini` points at it. `tests/conftest.py` sets a **dummy** `TYPESAFE_API_KEY` because two modules still build a client at import. The old root-level `test_*.py` scripts are left untouched and are not collected.

**Rejected:** making the clients lazy now (it is Phase 2's job), and deleting the old scripts now (Phase 7 converts or retires them).

**At 100x load:** n/a.

## D-016 — Unclassified requests are `needs_human`

**Chosen:** until Phase 2 adds a real human node, `unsupported_department` returns `needs_human` (`request_not_classified`) rather than a made-up decision string.

## D-017 — Legal no longer extracts a "primary citation" from the answer text

**Chosen:** drop the regex that turned `[BNS, Section 357]` into a "decision". The answer text and the retrieved sections are returned as they are.

**Rejected:** keeping it. With no `decision` field it has no job, and comparing the answer's claims to the retrieved sections is exactly what the Phase 4 verifier is for.
