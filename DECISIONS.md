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

---

# Phase 3 — missing information

## D-026 — Extraction: the model proposes, code decides what to believe

**Chosen:** `extraction.py` asks Claude Haiku on Bedrock (same client and model as routing, overridable with `EXTRACTION_MODEL_ID`) to record details the sentence *explicitly* states, through a forced tool call, at temperature 0, with the user's text fenced as data. Each proposed value must come with a verbatim quote. Code then accepts a value only if the quote appears word-for-word in the sentence, and (for numbers) the number is one of the numbers in that quote (it understands `65,000`, `₹65000`, `1,50,000`, `65k`, `1.5 lakh`, `2 crore`). Anything else is discarded as "not stated" and asked for on the form. If the model is unreachable the result is a form for everything, never an error.

**Rejected:** (a) trusting the model's JSON (a plausible wrong number would flow into a payment recommendation); (b) regex per field (brittle); (c) a different model (you did not object to Haiku when I proposed it; it is one env var to change).

**Known limit:** number words ("forty-five thousand") are not understood, so they fall back to the form. That is the safe direction.

**At 100x load:** one extra Haiku call for every finance request that is not already complete. It is not counted against the per-request LLM budget (departments are pure and cannot see state). Add that accounting and a circuit breaker if it matters.

## D-027 — Booleans are never extracted from prose

**Chosen:** "first-time vendor?" and "duplicate invoice number?" are always answered on the form (yes/no), never read from the sentence.

**Rejected:** extracting them. "Not a first-time vendor" read as "first-time vendor" flips the policy result, and a quote cannot prove polarity. A yes/no question costs the user two clicks.

**Consequence:** every AP request needs one form round. AR can complete from the sentence alone if all six numbers are stated.

## D-028 — Ask only for what the policy uses; derive the rest

**Chosen:** AP asks for invoice amount, first-time vendor, the vendor's usual invoice, duplicate invoice number, payment terms, and days since invoice. Code derives the ratio (amount ÷ usual invoice), "round number" and "overdue". `gst_rate_pct` and `interstate` are accepted if supplied but never asked, because the AP policy does not use them. A first-time vendor has no history, so the "usual invoice" is not required and the ratio rule is skipped (the model now allows a missing ratio only for first-time vendors). AR still asks for its six fields (the credit review validates them and hands to a human).

**Rejected:** asking for all ten stored fields (users cannot answer "ratio to vendor average") and inventing a ratio for first-time vendors (the synthetic data did, which is an artefact, not a fact).

**Compatibility:** the older complete 10-field record still works; if its derived flags disagree with the other fields the error names that field.

## D-029 — The backend describes the form; the frontend renders it

**Chosen:** a `needs_more_info` result may carry `follow_up`: the department's path, `collected` (what was understood), and an ordered list of field specs (name, label, kind, help, choices, and a `problem` when an earlier answer was unusable). A validator requires the specs to match `missing_fields` exactly. `FollowUpForm.tsx` knows nothing about finance.

**Rejected:** a field table in the frontend (that is the duplication defect #11 again), and a plain list of names (not enough to render number vs yes/no vs choice).

## D-030 — Follow-ups carry the department the system chose, and are not routed or extracted twice

**Chosen:** the console answers a form with `follow_up: {department, sub_department, fields}` plus the original sentence. The API validates the department against the five and skips routing (audit shows `source: follow_up`). The model is not asked again, except when the user has only just picked the path on that form (the sentence's numbers were not used yet because they belong to a path nobody had chosen).

**Rejected:** re-running the whole graph on the follow-up (a second router call that could pick a different department and discard the answers, and a second extraction call); and any frontend-chosen department (the form came from the system, the user still never picks).

**Trust note:** an API client can pin a department with `follow_up`. Phase 6 adds API-key auth; the same holds for `department_sequence`.

## D-031 — Provenance travels with the recommendation

**Chosen:** the finance evidence records, per value, whether it was "entered by the user", "stated in the request: “…”" (the quote), or "derived".

**Why:** a human approving a payment should know which numbers a model read from a sentence. Note: values that go through a form round come back as "entered by the user", because the form returns them; only a value extracted in the same request keeps its quote. That is a simplification, not a leak.

## D-032 — Why only Finance has an extraction step

**Chosen:** extraction is a generic mechanism (field definitions, grounding, coercion), but only Finance needs structured input. HR, Backend and Legal send free text to their services; Export sends a product description, and its service takes the whole sentence.

**Rejected:** extracting a product name for Export. It would add a model call and a new failure mode to a department that already works on free text. It is a clear next step if the HS-code question template ("What is the HS code for <whole sentence>?") proves awkward in real use.

---

# Phase 4 — a verifier that verifies

## D-033 — The verifier checks claims against the evidence text, and its own verdicts are checked

**Chosen:** for an `answered` result from HR, Export or Legal, a model lists the factual claims in the answer and judges each against the evidence text only (each evidence item's source, text and data, bounded to 16,000 characters), through a forced tool call at temperature 0, with question, answer and evidence fenced as data. A "supported" verdict must quote the evidence, and **code checks the quote really is in the evidence**; otherwise the verdict is downgraded to "not in evidence". Any claim that is contradicted or not in the evidence fails the answer. A checker that returns no usable claims is treated as a failure to check, never as a pass. Model: `VERIFIER_MODEL_ID` (defaults to the router's Haiku).

**Rejected:** (a) the old check (decision, confidence, rationale and labels, "internal consistency" only, and only inside a confidence band); (b) trusting the checker's verdicts without grounding (an LLM judge that says "supported" with no proof is the same weakness as the thing it checks); (c) a numeric-overlap heuristic as the main check (it would flag legitimate numbers the evidence summary does not contain).

**What this does not make true:** the checker is itself a model. Quote-grounding removes invented support, not misjudged support. Mutation tests confirm the code-level guards (quote check, retry cap, withdrawal, "unchecked" labelling) each fail loudly if broken; they cannot show the model judges well. That needs the live eval (Phase 7).

**At 100x load:** one extra Haiku call per answered HR/Export/Legal result, two if retried, counted against the per-request LLM budget (5). A Bedrock slowdown now directly slows every answer; see D-037.

## D-034 — What is never sent to the checker, and how "unchecked" is reported

**Chosen:** results that are not `answered` (needs_human, needs_more_info, failed) are not checked: there is no answer to verify. Finance is rule-based (a deterministic policy outcome). Backend returns no source material (the ticketing agent gives a category and an answer, nothing to check against). Finance and Backend answers pass with `verification: {status: "unchecked", ...}`. The API returns `verification` and the UI says "Not independently checked" and why. Nothing is ever labelled "verified" unless a claim check or a cached verified answer backs it.

**Rejected:** passing Backend silently as if verified; sending it to the checker with no evidence (the checker would mark everything "not in evidence" and every Backend answer would be withdrawn).

**Consequence you may not like:** a Backend answer is released without any independent check. That is the honest state of what that service exposes. The fix is on the service side (return its sources), not in this repo.

## D-035 — One retry, only with specific feedback, and only where feedback can be used

**Chosen:** the blind retry (same department, same input, up to 3 attempts) is gone. On a failed claim check, HR, Export and Legal get exactly one retry, and the department receives the specific unsupported claims (for example: "the fine is five thousand rupees: not found in the evidence"). The feedback is added to the question sent to the service in a clearly marked bracketed note. HR's complaint gate does not see it. Export's answer now states which product the code is for, so "this code matches what was asked" is a testable claim and a mismatch produces specific feedback. Finance, Backend, unknown departments, and any `failed` result are never retried.

**Rejected:** keeping a cap of 3 (nothing new is learned between identical attempts); retrying without feedback; retrying `failed` results (that only repeats a slow call against a service that is down).

**Unverified:** whether the Legal RAG, NiryatAI and HR agent services actually use the appended note to produce a better answer. I could not test any of them. The mechanism is tested; its effectiveness is for the live eval.

## D-036 — An answer that fails verification is withdrawn, never released as `answered`

**Chosen:** after the one retry, or immediately for a department with no retry, a failing result is rewritten to `needs_human` with reason `verification_failed`. The draft answer is kept (clearly labelled "not verified" in the UI) and the verifier's findings are appended to the evidence as a `verifier` item, so the reviewer sees exactly which statements were unsupported.

**Rejected:** leaving the result as `answered` with a flag (the earlier design did this and the UI/API would show an unverified answer as an answer).

## D-037 — If the checker itself is unavailable, the default is to withhold the answer

**Chosen:** a checker error, no LLM budget, or an unusable reply withdraws the answer with reason `verification_unavailable`. `VERIFIER_ON_UNAVAILABLE=release_unchecked` is available for a deliberately degraded deployment: answers are then released labelled `unchecked`, and are never cached.

**Rejected:** failing open by default. HR, Export and Legal are the departments where a wrong answer matters, and "nobody could check it" should not look the same as "it was checked".

**At 100x load / outage:** a Bedrock outage turns every HR, Export and Legal answer into a human review. That is the intended fail-closed behaviour but it needs an operator alert; Phase 6's `/ready` will report it.

## D-038 — Only verified answers are cached

**Chosen:** `cache_store` requires `verification.status == "verified"`. An answer released as `unchecked` is not stored. A cache hit is labelled verified because only verified answers are ever stored.

## D-039 — Expect a high escalation rate for Legal until its evidence improves

**Why:** the Legal service exposes each retrieved section's title and a short summary, not the raw statute text (D-008). The answer was generated from the full text. A claim the answer makes that appears only in the full text will be "not in evidence" against a summary, so the check will withdraw some correct answers. That is the honest consequence of checking against what we can actually see.

**What fixes it:** have `legal-rag-chatbot` return the retrieved passage text in its citations; `Evidence.text` then carries it with no change here. I cannot measure the real rate without that service. Treat the first live eval run as the measurement.

## D-040 — Scope limits

The sequential mode (`department_sequence`) still verifies only the last department's result, as before. The `llm_budget_remaining` of 5 per request covers routing plus up to two verifications; a request that needs more fails closed (`verification_unavailable`) rather than skipping the check.

---

# Phase 5 — real human-in-the-loop

## D-041 — Durable run state: SQLite by default, Postgres when `CHECKPOINT_DB_URL` is set

**Chosen:** every run is checkpointed (LangGraph `SqliteSaver`, or `PostgresSaver` over a connection pool when `CHECKPOINT_DB_URL` is set). SQLite path: `CHECKPOINT_DB_PATH` (default `data/checkpoints.sqlite`, git-ignored), WAL mode, 30 s busy timeout. The graph and storage are built on first use (`runtime.py`), not at import. The database URL carries credentials and is never logged (a test fails if it is).

**Verified:** against a real SQLite file and a real Postgres 16 (throwaway local instance), including a simulated restart, and once with a real process kill and restart.

**Rejected:** in-memory checkpoints (a restart would lose every waiting request; a test proves it fails), and Postgres-only (needs a server just to try the project).

**Things this creates:** (a) the database holds each user's request and the evidence at rest. Treat it as sensitive: restrict access, encrypt the disk, decide a retention period. (b) every run writes about 8 checkpoints, including runs that never needed a person. There is no cleanup yet. (c) Postgres setup creates its tables at first start, so the database user needs DDL rights. (d) At 100x load the checkpoint tables are the first thing to grow; add retention (delete completed runs after N days) before then.

## D-042 — Every `needs_human` now waits for a person

**Chosen:** a result a department marks `needs_human` (a complaint, a security keyword, a payment, a withdrawn answer, an unclassifiable request) now goes to `queue_review` then `human_review`, which pauses the run with `interrupt()`. Before, only a verifier failure reached the human node and every other "needs a human" simply ended the run with a flag.

**API change:** `POST /request` returns `run_id` and `pending_review`. `needs_human` is still true while waiting and becomes false once a person decides.

**Rejected:** leaving department-level `needs_human` as a flag (that is defect #6).

## D-043 — The run id is generated by the server

**Chosen:** `run_id = uuid4` is the checkpoint thread id. The client's `request_id` is kept only as a label.

**Rejected:** using the client's `request_id` as the thread id. The console generates 6-character ids; a collision (or a client reusing one) would merge a new request into another run's saved state and its audit trail. A test sends two requests with the same `request_id` and checks they stay separate.

**Consequence:** the run id is a 128-bit random value, so it works as an unguessable reference for the end user to check on their request.

## D-044 — A review inbox table, with an atomic claim

**Chosen:** a small `reviews` table in the same database, one row per paused run (department, reason, the question, whether a draft exists, status). "List pending" is one indexed query. `claim` is a single atomic UPDATE (pending → resuming), so of two simultaneous resumes exactly one runs and the other gets 409; if the resume fails the claim is released and the review is pending again.

**Rejected:** scanning every checkpoint to find paused runs (cost grows with all history, not with pending work); a second store (more to run).

**Verified:** 16 threads racing to claim one review produce exactly one winner, on SQLite and on Postgres.

## D-045 — What a reviewer can decide

**Chosen:** three actions. **approve** accepts the draft (only if there is one). **answer** lets the person write the answer; it does not inherit the draft's evidence. **reject** declines with a note: the result is `failed` with reason `declined_by_reviewer`, shown to the user as "a person reviewed this and declined it", not as a service failure. The result's evidence always includes a `human_review` item with who decided and the note. The verification is `human_approved` / `human_provided`, never "verified", and human decisions are never cached.

**Rejected:** a fifth result status for "declined" (the contract has four, and `failed` plus a reason carries it); letting a person approve with no draft.

## D-046 — Two nodes, so the held result is saved before the pause

**Chosen:** `queue_review` runs once, builds the held `needs_human` result if none exists, and files the run in the inbox. `human_review` then does only `interrupt()` and applies the decision. LangGraph re-runs the interrupted node from its start on resume, so any side effect inside it would run twice; keeping the inbox write in the earlier node avoids that, and means the saved state at the pause already contains the result a reviewer needs.

## D-047 — IMPORTANT: the reviewer endpoints have no authentication yet

**State today:** `GET /reviews`, `GET /runs/{id}` and `POST /reviews/{id}/resume` on the Python API are open, and the reviewer name is whatever the caller types. Anyone who can reach the API can approve a held payment recommendation. **Do not expose the Python API to a network you do not control until Phase 6 adds API-key auth.**

**Mitigation in place:** the public console exposes only a read-only lookup by run id (`GET /api/agent/runs/{id}`); it does not expose listing or resuming, and a test checks that.

**Also for Phase 6:** map an API key to a named reviewer so the recorded reviewer is not self-declared.

## D-048 — Reviewers see the question; the list truncates it

The inbox returns the user's request text (truncated to 500 characters in the list; 2,000 stored). It can contain personal data, which is one more reason for D-047.

---

# Phase 6 — dependencies and deployment

## D-049 — MCP: renamed and removed, taking my recommended default (you did not choose)

**Chosen:** the `*_mcp.py` files are now `legal_client.py`, `hr_client.py`, `ticketing_client.py` and `niryat_client.py`. The FastMCP wrappers and the `mcp` dependency are gone, `ticket_router_mcp.py` (imported by nothing, and pointing at a plain-HTTP raw IP) is deleted, and `docs/MCP_SERVERS.md` is replaced by `docs/SERVICES.md`, which says plainly that these are HTTP services.

**Why this was a default and not your decision:** I asked which you wanted, twice, and got no answer. I took my recommendation because the code never started an MCP server or client (Phase 0, defect #7), so the rename loses no capability. **It is one commit (the first of Phase 6); `git revert` undoes it** if you want real MCP clients instead.

**Rejected:** real MCP clients in the request path. They add a process per service, startup and hang failure modes, and no capability, unless some other tool needs these as MCP servers. If that is the plan, say so.

## D-050 — Every address and timeout from the environment; errors reduced to a safe code

**Chosen:** `services.py` is the one place that calls a service. Addresses (`LEGAL_QUERY_URL`, `HR_AGENT_QUERY_URL`, `TICKETING_URL`, `NIRYAT_CHAT_URL`, `NIRYAT_TRADEDATA_URL`) and timeouts (`*_TIMEOUT_SECONDS`) come from the environment, read at call time. **There are no built-in defaults:** the old defaults were `localhost` ports and a CloudFront host the old comments said was terminated. An unset or non-http(s) address returns `not_configured` naming the variable. Errors become a short code (`timeout`, `http_status` with the status code, `unreachable` with only the exception type, `bad_response`). The exception message and the remote body go to the server log only. A test fails if a message, a body, or a hard-coded address reappears.

**HR:** because the HR agent no longer runs a local Ollama model (you moved it to Haiku), its default timeout drops from 280 s to 60 s, still overridable. I have not seen its real reply, so the `{answer, evidence}` shape is still an assumption (D-008).

**Rejected:** keeping defaults "for convenience" (a wrong default silently calls the wrong or a dead system); passing the remote body through "for debugging" (it can carry another system's internals).

**Known small gap:** a service that answers HTTP 200 with its own `{"error": ...}` body is treated as an error and its `detail` (truncated to 300 characters) is shown. That is the remote's own message, not an exception.

## D-051 — Runtime dependencies pruned and bounded

**Chosen:** `requirements.txt` lists only what `uvicorn api:app` imports (langgraph, the SQLite checkpointer, pydantic, fastapi, uvicorn, httpx, numpy, anthropic[bedrock], boto3, typesafe-sdk), each bounded to the next major version from the versions I tested. Removed: `mcp`, `xgboost`, `scikit-learn`, `pandas`, `joblib`, `faker` (used only by the offline training scripts, now `requirements-train.txt`). Verified: a clean virtualenv with only `requirements.txt` imports the real app with no keys and no database. Postgres and local embeddings stay optional extras.

**At 100x load / supply chain:** bounds, not a lock file. A lock file (pip-compile) is the next step for reproducible builds.

## D-052 — A hard deadline per department attempt

**Chosen:** `run_department` runs each department in a worker thread and waits at most `DEPARTMENT_TIMEOUT_SECONDS` (default 180 s, deliberately above Export's two service timeouts of 60 + 90 s). On expiry it returns `failed` with `timed_out: <department> did not finish within Ns` and the graph carries on. An unexpected exception is `failed` with only its type; a `TimeoutError` raised by the department itself is reported as an unexpected error, not as the deadline.

**Honest limit:** a Python thread cannot be killed. An abandoned call keeps running until its own service timeout fires. If every one of the 64 workers (`DEPARTMENT_WORKERS`) were stuck, new attempts would queue and then time out too, so requests still return, but throughput would stall until the services recover.

**Rejected:** relying on each service's own timeout alone (a bug in any client would hang a request).

## D-053 — `/ready` reports which departments can work; a down service means degraded, not unready

**Chosen:** `GET /ready` checks storage (a real query, bounded in time), authentication, whether the router key is set, and probes each department's address (any HTTP reply means reachable; connection error or timeout means unreachable; 5xx means unhealthy; an unset address means not configured). Overall: `ready`, `degraded` (a department or the router is unavailable; the API still answers, and that department returns `failed`), or `not_ready` (storage down or authentication not configured; HTTP 503). Probes run concurrently and are cached for 5 s so the endpoint cannot be used to hammer the services. It returns status words and variable names, never addresses. `<NAME>_PROBE_URL` lets a service be probed at a different address (for example a `/health` route).

**Not probed:** the router key and AWS access are reported only as present or absent. Checking Bedrock reachability would cost a model call.

**Found while testing:** with Postgres down, each call waited 30 s inside the connection pool. Connect and pool-wait timeouts are now set (5 s and 10 s, configurable) and `/ready` bounds its own check, so a dead database fails fast.

**Rejected:** making a down department fail the whole `/ready` (an orchestrator would then restart a healthy API because one downstream service is down).

## D-054 — API-key authentication, two roles, fail closed

**Chosen:** `API_KEYS=name:role:key,...` with roles `client` (submit, look up a run) and `reviewer` (also list and decide reviews). Bearer token, constant-time comparison against every key, keys at least 24 characters, never logged or echoed. **The reviewer recorded on a decision is the name of the key used; a name in the request body is ignored** (a test sends "the CEO" and checks `asha` is recorded). `/health` and `/ready` are open. With no valid keys configured the protected endpoints answer 503 rather than letting everyone in. `AGENT_AUTH_DISABLED=true` turns authentication off explicitly for local development; the caller is then anonymous and must give a reviewer name.

**This closes D-047:** the reviewer endpoints are no longer open.

**The console** sends its own `client` key from its server-side routes (`AGENT_COMPANY_API_KEY`); a browser test checks the key never appears in what the browser receives.

**Not done:** rate limiting or lockout after repeated bad keys; keys are long random values, but a gateway should rate-limit. No key rotation tooling: change the environment and restart. At 100x load, keys in an environment variable should become a secret store and per-person identities.

## D-055 — A 500 carries an incident id, never the exception

**Chosen:** an unexpected error returns `Something went wrong on our side. Incident <id>.`; the real exception and traceback go to the server log under that id. This covers `/request` and, through a global handler, every other endpoint. Department and service failures are not 500s at all: they are `failed` results with a safe code (D-050).

**Rejected:** `str(exception)` in the response (the old behaviour; it can carry hostnames, SQL, paths and secrets).

## D-056 — Deployment: what is in the image and what I could not verify

**Chosen:** the Dockerfile installs only `requirements.txt`, runs as a non-root user, keeps SQLite in a volume (`/app/data`) unless `CHECKPOINT_DB_URL` is set, has a healthcheck on `/health`, and starts one worker (the answer cache is per process). `.dockerignore` keeps the image to the 36 files the API imports (no tests, frontend, models, training scripts or docs). `build-essential` and the unused `MODEL_BASE_PATH` are gone.

**Verified:** I applied the `.dockerignore` to a copy of the repo and ran the real server from it in a virtualenv with only `requirements.txt` installed, then exercised health, ready, auth and the full review flow over HTTP.

**NOT verified:** there is no Docker daemon in this sandbox, so **I have not built the image**. Please run `docker build .` once; the file set and dependencies are proven, but the Dockerfile text itself has not been executed.

**Scaling note:** SQLite plus one worker is a single-machine setup. Several workers or machines need Postgres (`CHECKPOINT_DB_URL`) and a shared cache.

## D-057 — HR hosting: your change to Haiku, and what is still unknown

**What you told me:** the HR SQL agent now uses Haiku. That removes the Ollama-on-a-laptop dependency, so HR needs only an address (`HR_AGENT_QUERY_URL`) and no GPU. Its timeout default fell from 280 s to 60 s.

**Still unknown to me:** where the HR service runs (it can be any host the API can reach), whether it has a health route (use `HR_AGENT_QUERY_URL_PROBE_URL` if so), and its real reply shape. I have still not seen an actual response; the `{answer, evidence}` shape is from its old documentation. If it returns no `evidence`, answers are held for a person (D-008). A sample response would settle this.

---

# Phase 7 — tests and evals

## D-058 — One pytest suite; the old scripts are deleted

**Chosen:** `tests/` is the suite (433 tests with Postgres, 409 without), run by `python -m pytest`, no keys, every external service mocked. The eleven root-level `test_*.py` scripts are deleted. Each was either superseded by `tests/`, tested code that no longer exists (the DistilBERT backend, the CrewAI HR path, the three-times retry cap, the confidence-band verifier), or needed a live service. The one pure test worth keeping (HS-code extraction) moved into `tests/test_clients.py`. Tests run on Python 3.11 (the Docker image) and 3.13.

**Postgres:** the 24 Postgres tests skip unless `TEST_POSTGRES_URL` is set, so the suite runs anywhere. CI sets it with a Postgres service and **fails if those tests were skipped**, so they cannot silently stop running.

**A flaky test, found and fixed:** on one Python 3.13 run a cache test failed. Cause: my test embedder used Python's `hash()`, which is randomised per process, so for some seeds two different words got identical vectors and the cache correctly treated them as one question. I reproduced it deterministically (`PYTHONHASHSEED=926` fails the old test and passes the new one), switched to `md5`, and ran the file under 11 other seeds. It is a rare collision (about 1 in 500 per word pair), not a frequent one, and not a product bug.

**Rejected:** keeping the old scripts "for reference" (they would fail or mislead and they are in git history).

## D-059 — The CI workflow, and what I could and could not run

**Chosen:** `.github/workflows/ci.yml` has three jobs. **python**: a Postgres 16 service, Python 3.11 and 3.13, `pytest` with no secrets, and a step that fails if a Postgres test was skipped. **frontend**: `npm ci`, lint, build, Playwright against a mock of the API. **docker**: build the image, start it, check `/health`, check that a request without a key is 401 and with a key is 200. It needs no secrets, so a pull request from a fork runs identically. The live eval is **not** in CI.

**What I ran:** the workflow's YAML parses and passes `actionlint`. I ran the python job's exact commands in fresh virtualenvs on 3.11 and 3.13 with a real Postgres, and the frontend commands. **I could not run GitHub Actions, and I could not run the docker job (no Docker daemon).** The first CI run is the first real test of the workflow and of the Dockerfile.

## D-060 — Evals: deterministic checks decide, a model only comments

**Chosen:** `evals/run_evals.py` sends each case to the API and checks the result with code: status, department, reason, verification label, required words, evidence present, no traceback/path/password anywhere. An optional judge (Gemini or Grok) comments on answer quality and **never** changes a pass or fail. A call that could not be made is `ERROR`, never a pass, and the exit code is non-zero for either.

**Coverage is enforced:** 20 cases cover every department's `answered`, `needs_human`, `needs_more_info` and `failed` paths. Cells that cannot happen by design (Finance never answers; free-text departments never ask for fields) are listed with the reason, and a unit test fails if a cell has neither a case nor a reason.

**Failed paths are observed, not assumed:** for "service down" cases the runner starts a local copy of the API with just those addresses pointed at a closed port, using a throwaway SQLite file and a generated key, never your database. A test starts a real one and checks `/ready` reports the chosen service unreachable.

**Rejected:** judging pass/fail with a model (the old approach: it scored a wrong answer 4/5 with a lenient judge and its verdicts could not be reproduced); mocking the failures in the live eval (a mocked failure proves nothing about the real system).

**Deleted:** the old `evals.md` and `ragas_eval.md`. They reported on a system that no longer exists and their numbers cannot be compared with anything the new tools produce. They remain in git history.

**Not verified:** the eval has never run against real services, and `run_ragas.py` has never run at all (no key). Its helpers are tested; its first run is its test. The HR ground truth (Sales = 1035) is specific to your dataset.

## D-061 — Dead code: deleted, and one deliberate exception to what I announced

**Deleted:** `complaint_agent.py` (CrewAI/torch HR path, hard-coded local paths, a placeholder key), `backend_model.py` (the old DistilBERT path), the old test scripts, `docs/ROUTING_REGRESSION.md` and `demo/index.html` (both describe the removed system).

**Left in place, which I had said I would delete:** `models/finance/*.pkl`, `train_ap_classifier.py`, `synthetic_data_generator.py` and `syntheic_data/`. Nothing in the running system uses them, but an earlier comment in `finance.py` said an outside tool ("Company Desk") evaluates the pickles, and I cannot see that tool. Deleting something an unseen consumer may read is not recoverable by me, whereas leaving it costs only clutter, and the pickles are excluded from the Docker image. Pickles can run code when loaded, so please check whether anything still needs them and delete them if not.

## D-062 — Documentation

`README.md` is rewritten from the code as it now stands (what runs, how to run, test, deploy and eval it, and what is not true yet). `HOW_IT_WORKS.md` is rewritten for the rebuilt system and keeps the Phase 0 defect list with what happened to each. `docs/SERVICES.md` replaced the MCP document. `frontend/README.md` was rewritten in Phase 2 and updated since.
