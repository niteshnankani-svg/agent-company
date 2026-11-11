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

---

# Phase 2 — routing

## D-018 — Classifier clients are created on first use, with SDK-native timeout and retry

**Chosen:** `clients.py` builds the TypeSafe and Bedrock clients lazily (`lru_cache`), so importing the app needs no key and no network. Timeouts and retries use the SDKs' own settings: TypeSafe 10 s per attempt, 2 retries with backoff within a 25 s total budget (connection errors, timeouts, 408/429/5xx); Bedrock 10 s, 1 retry. All overridable by env (`TYPESAFE_TIMEOUT_SECONDS`, `TYPESAFE_MAX_RETRIES`, `TYPESAFE_RETRY_BUDGET_SECONDS`, `BEDROCK_*`, `ROUTER_MODEL_ID`). Region and model ID were hard-coded and are now env-configurable with the old values as defaults. HR's complaint gate uses the same client.

**Rejected:** writing our own retry loop (the SDKs already do it, with backoff and `Retry-After`); creating clients in module scope and catching the failure (that is the defect: tests and startup needed a key).

**Verified by test:** a server that always answers 503 produces exactly 3 attempts (1 + 2 retries) and then an exception; importing `api` in a fresh interpreter with no key succeeds; with no key, a request ends in human review with `classifier_unavailable`.

**Worst case to a routing decision when both services hang:** about 25 s (TypeSafe) + 2 × 10 s (Bedrock) ≈ 45 s.

**At 100x load:** retries multiply traffic against a service that is already failing (up to 3x). Needs a circuit breaker and a shared rate limit; neither exists yet.

## D-019 — What the router does when it cannot decide

**Chosen:** Jev's answer is used only if the department name is one of the five and its confidence ≥ `ROUTER_MIN_CONFIDENCE` (default 0.75, unchanged). Otherwise the LLM is asked; if it answers a valid name, that is used. If neither yields a department the request goes to a human with one of two reasons: `request_not_classified` (they answered and were unsure) or `classifier_unavailable` (any of them errored, or no key/budget).

**Rejected:** (a) trusting any string Jev returns (a made-up department would crash routing downstream); (b) one generic "escalate" reason, because "the system is down" and "the request is ambiguous" need different responses from an operator.

**Also changed:** routing is no longer cached (see D-021).

**At 100x load:** every request costs one Jev call, plus one Bedrock call when Jev is unsure. LLM budget stays 5 per request.

## D-020 — One explicit human-review node

**Chosen:** the router's `"human_escalation"` and the verifier's retry cap now both go to a single node `human_review`, by name. It always yields a `needs_human` result with a reason, keeps any result the department already produced, and sets `escalation_reason`. It replaces `unsupported_department` and the old `human_escalation` node.

**Rejected:** keeping two nodes, and keeping the implicit fall-through that caused defect #5.

**Honest limit:** today it records the hand-off and ends the run. It does not pause or resume; that is Phase 5, when this node gets `interrupt()` and a checkpointer.

**At 100x load:** n/a until Phase 5.

## D-021 — Semantic answer cache: layered conditions, not just similarity

**Chosen:** `cache.py` is replaced. A stored answer is reused only if all hold: department is declared cacheable; no personal identifiers or first-person wording in the request; same department and scope (Legal: the act filter); identical **discriminators** (every number/identifier, all-caps acronym and mid-sentence capitalised word); not expired (TTL, default 24 h); and embedding similarity ≥ threshold (default 0.95). Lookup happens before the department runs (a hit skips it); storing happens after the verifier passed the result (`cache_store` node), never before. Only `answered` results are stored. Sequential runs are never cached. LRU eviction at 1000 entries.

**Why layered:** embeddings fix paraphrases but not the original bug. I tested it: with the discriminator guard off, "Section 357" vs "Section 358" is a hit at cosine > 0.8 (a test fails if the guard stops working).

**What may never be cached, and why:** Finance (a person's or invoice's data), HR (live employee data), Backend (a specific ticket and person), Export (must be checked against live TradeStat every time). **Only Legal is cacheable**, because its answers come from a fixed statute corpus. Unknown departments are not cacheable (deny by default).

**Rejected:** (a) embedding similarity alone (collides on numbers and acronyms); (b) caching routing decisions (saves a call only if the embedding is cheaper than the classification, and a wrong entry sends a request to the wrong department); (c) letting the department write its own result into the cache before verification (the old design rule: only verified output is cached); (d) NER to detect person names (a heavy dependency for a guess); the department-level rule is the real guarantee and the text rules are belt and braces. **A question that names a person without a pronoun, PII pattern or capitalised mismatch could still be cached**; for Legal the answer comes from statute text, but this is a known gap.

**Honest limits:** the 0.95 threshold is a placeholder. Calibrating it needs the real embedding model and a labelled set of near-miss questions; that belongs in the live eval (Phase 7). The cache is per-process memory: per worker, lost on restart.

**At 100x load:** per-worker memory means hit rate drops as workers are added; the scan is over entries with matching discriminators (small), but each Legal request now makes one embedding call even on a miss. Move to a shared store (Redis or pgvector) with the same policy layer.

## D-022 — Embedding provider is a deployment setting; the cache is off until one is set

**Chosen:** `EMBEDDING_PROVIDER` = `bedrock` (Titan text embeddings, default model `amazon.titan-embed-text-v2:0`) or `local` (sentence-transformers, optional large dependency). Unset means no cache, which is always safe. An unknown value logs an error and disables the cache. An embedder failure is a cache miss, never a request error. Nothing that policy forbids is ever sent to the embedding service.

**Why this way:** the owner has not yet chosen a provider and I cannot test either against a real service here. Both adapters are small; the Bedrock one is tested with a stub client against the documented Titan request/response format, **not against AWS**. The local one is untested because it needs a model download.

**Rejected:** guessing a provider and making the cache depend on it.

**At 100x load:** Bedrock embeddings add a network call per cacheable request and a per-token cost; local embeddings add CPU and memory per worker.

## D-023 — The frontend is a thin proxy to the Python API, with no fallback

**Chosen:** `frontend/app/api/agent/request/route.ts` validates the input (non-empty, ≤ 8000 chars), forwards `{request_id, raw_input}` to `AGENT_COMPANY_API_URL`, and returns the API's answer. If the API is unset, down, slow or errors, the user gets a short error. The upstream error body is never forwarded. Deleted: the TypeScript router (`lib/orchestrator.ts`), the in-process TF-IDF "backend" model (`helpdeskModel.ts`, `helpdesk-model.json`), the canned replies for the other four departments, the `@typesafe-ai/sdk` dependency, and all the regexes and criteria copied from Python. A test fails if any of that reappears.

**Rejected:** (a) keeping the in-process fallback "for resilience": it showed answers from a different system without saying so (defect #11). (b) The browser calling the Python API directly: the API will need a key (Phase 6) that must not be in browser code, and the API sends no CORS headers.

**At 100x load:** each request holds a serverless function open for up to 55 s while the graph runs. At volume this should become submit-then-poll, which the Phase 5 resume endpoints make possible.

## D-024 — The UI is one free-text box

**Chosen:** one textarea and an Ask button. Removed: the "advanced" view, the department scenario tabs and forms, the department-labelled example buttons (examples are now plain sentences), the sequential pipeline view, and the DistilBERT panel. The result panel renders by status: `answered` (answer, expandable evidence, and a confidence figure **only** when the department has a real one, labelled with what it is, e.g. "retrieval score… not the chance the answer is right"); `needs_human` (plain-language reason; any draft is labelled "not verified"); `needs_more_info` (the missing field names); `failed` (the error). The animated trace now reads the new audit `status` field.

**Known gaps, on purpose:** `needs_more_info` lists the fields but there is no follow-up form yet (Phase 3). The Python API still accepts a caller-supplied `department_sequence` (used by tests and API clients); the UI never sends it. If "nobody picks a department" must hold for API clients too, that parameter should be removed; say so and I will.

**Also fixed:** the first trace node was clipped at the left edge.

## D-025 — Frontend tests run against a mock of the Python API

**Chosen:** the Playwright run starts `tests/mock-api.mjs` and points the console at it, so the 12 tests need no backend and no keys. They cover each status, no department picker, only `{request_id, raw_input}` forwarded, upstream errors not leaking, input validation, and no router code in the frontend source. `PW_CHROMIUM_PATH` optionally selects an installed Chromium (needed in this sandbox, harmless elsewhere).

**Rejected:** tests against a live backend (they could not run in CI or without keys).

**Not covered:** the mock proves the console's behaviour, not the real API's output. I separately ran the real Python app with no keys behind the real console and confirmed it returns `needs_human / classifier_unavailable` and the UI shows it.
