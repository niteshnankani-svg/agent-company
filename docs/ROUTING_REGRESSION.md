# Ticket routing regression — analysis & fix

**Scope:** `agent_company/` (the "Agent Company API" LangGraph service the
frontend at `NEXT_PUBLIC_API_BASE_URL` calls). Not the `Multi_agent_ticketing_system/`
sibling project.

## Symptoms reported

1. Every ticket ends up in the **backend / ticketing** department.
2. Every ticket ends up **needing human review**.
3. "It wasn't like this before" — the DistilBERT model "changed its view".

Both symptoms are real and were reproduced locally. They come from **two
independent causes**, one in the orchestrator and one in the backend node.

---

## Cause 1 — the backend node escalates 100% of tickets (symptom 2 & 3)

`backend_ticketing.py::backend_head_node` builds a `reason` string for every
branch, then **unconditionally** returns:

```python
output = DepartmentOutput(decision='human_escalation', confidence=..., evidence_ids=[], ...)
```

There is no branch that produces a non-escalation decision. A confident, benign
prediction is treated identically to a security incident:

| ticket | DistilBERT result | old decision |
|---|---|---|
| "laptop battery drains in 20 min" | Hardware / Desktop Support — 0.97 | `human_escalation` |
| "cannot connect to office WiFi" | Network / Network Team — 0.81 | `human_escalation` |
| "suspicious login, possible breach" | Security / Security Team — 0.97 | `human_escalation` |

The model's output is written to `state['backend_triage']` as an advisory blob
and then ignored for the actual decision. That is what "the model changed its
view" describes — it still predicts fine, its prediction just no longer does
anything.

This is not a corruption; it was the design of the first BERT integration in
this project. `test_backend_model.py` *encodes* it
(`assertEqual(...decision, 'human_escalation')`, `assertEqual(...evidence_ids, [])`,
`assertTrue(...needs_human)`). The rationale in the code —
"this adapter has no approved resolution source" — is a valid concern about not
fabricating *resolution* evidence, but it was over-applied: it also removed the
ability to **route** a confident ticket to the right queue, which a
classification model legitimately can do.

### "Before"

The pre-rebuild system (`Multi_agent_ticketing_system/legacy/graph.py`) used a
single trained router with a **confidence gate at 0.70**: above the gate each
specialist node produced an answer; only sub-0.70 tickets hit `interrupt()` for
a human. Most tickets were answered. That is the behaviour being remembered.

---

## Cause 2 — the orchestrator funnels unmatched tickets to backend (symptom 1)

`orchestrator.py::local_classify` is a keyword classifier. Its
`backend_ticketing` list had **5 words**: `error, bug, ticket, system down,
crash`. Real IT tickets ("laptop won't boot", "VPN keeps dropping", "reset my
password", "Outlook won't sync") match none of them, so:

```
local_classify() -> (None, 0.0)
        -> llm_classify()  (Claude Haiku via Bedrock)
        -> Haiku is asked to pick one of 5 company departments for an IT ticket
        -> picks "backend_ticketing" for essentially everything technical
```

Reproduced: `"My laptop will not boot, black screen"` → `local_classify` returns
`(None, 0.0)` → `llm_classify` returns `('backend_ticketing', 0.85)`.

Because the test corpus is a helpdesk dataset (every row *is* an IT ticket),
**every** test ticket routes to `backend_ticketing` — and then Cause 1
escalates all of them. The two bugs compound into "everything is backend +
everything is human review".

Finance / HR / Legal / Export tickets with obvious keywords still route
correctly; the funnel only affects tickets the keyword list misses.

---

## Fix applied

### `orchestrator.py` — widen the keyword map
Ported the richer keyword lists from the sibling workflow (~45 IT terms plus
more finance / HR / legal / export terms). Effect: real IT tickets now classify
locally to `backend_ticketing` with confidence ≥ 0.75 and **never reach the
Bedrock fallback** — routing is now deterministic and works offline. HR keywords
are ordered before backend so "ticket about my payroll" resolves to HR on a tie.

### `backend_ticketing.py` — route confident benign tickets to the suggested team
New decision branch. `ROUTE_MARGIN = 0.65`.

| condition | decision | needs human? |
|---|---|---|
| invalid input / prompt injection | `human_escalation` | yes |
| security signal or category = Security | `human_escalation` (Security Team) | yes |
| category = Access | `human_escalation` | yes |
| model not confident (`accepted` is false) | `human_escalation` | yes |
| accepted but score < 0.65 | `human_escalation` (`it_classification_low_margin`) | yes |
| **accepted, benign, score ≥ 0.65** | **`route_to_team:<suggested_team>`** | **no** |

The routed decision carries real classification evidence
(`distilbert:<version>`, `it_category:<x>`, `suggested_team:<x>`) so the verifier
passes it on the rule check (non-escalation, non-empty evidence, confidence
≥ 0.65) with no LLM call. `escalation_reason` is left `None`, so the API's
`needs_human` is `False`. The model classifies and assigns a queue — it does
**not** claim to have resolved the ticket; the assigned team does that.

### Tests updated
`test_backend_model.py`: the "Application crash" cases now expect
`route_to_team:Application Support` and `needs_human == False`. Security /
Access / uncertain / injection / missing-model cases are unchanged — they still
escalate.

---

## Verified after the fix

End-to-end through `build_graph()`, offline (no Bedrock call — every ticket now
matches the local keyword classifier):

| ticket | routed to | decision | needs human |
|---|---|---|---|
| "laptop will not boot, black screen" | backend | `route_to_team:Desktop Support` | no |
| "VPN keeps disconnecting" | backend | `route_to_team:Network Team` | no |
| "Outlook won't sync, password prompt loop" | backend | `route_to_team:Application Support` | no |
| "app crashes on startup after update" | backend | `route_to_team:Application Support` | no |
| "suspicious login, possible breach" | backend | `human_escalation` (Security Team) | yes |
| "grant me access to the finance folder" | backend | `human_escalation` (Access) | yes |
| "double charged on invoice 4491, refund" | finance | `human_escalation`\* | yes |
| "HS code for cotton t-shirts to Germany" | export | (needs structured fields)† | — |

\* Finance's credit model is separately quarantined
(`credit_model_not_validated`) — unrelated to routing.
† `hr_head_node` / `legal_head_node` / `export_head_node` read required keys out
of `department_fields` with **no fallback to `raw_input`**, so a bare
`{request_id, raw_input}` submission to those departments raises `TypeError` →
HTTP 500. Pre-existing (the frontend `AskConsole.tsx` already works around it for
its own forms); the backend department is unaffected because
`backend_head_node` reads only `raw_input`. Fix separately if free-text
submissions should reach HR/Legal/Export.

`test_backend_model.py` (8 tests) passes. `test_sequential.py` fails on the
finance credit-model quarantine described above, not on routing.

## Retry routing — fixed

`graph.py::route_after_verifier` used to map **every** retry to `finance_head`:

```python
{"done": END, "retry": "finance_head", "human_escalation": "human_escalation"}
```

So a backend / HR / legal / export decision that failed verification was retried
through the **finance** node. Now `route_after_verifier` dispatches on
`state["active_department"]` (every head sets it) back to that department's own
head, via `_RETRY_TARGETS`; an unknown/unset `active_department` fails closed to
`human_escalation` rather than silently retrying the wrong department. The
verifier edge map lists all five heads as retry targets.

Verified with the verifier forced to fail: a backend ticket now loops
`backend_head → verifier` three times, then caps out to `human_escalation` with
the correct `escalation_reason`. `test_cap_logic.py` updated to assert the
per-department retry target and the fail-closed fallback; `test_graph.py`
(finance retry-cap scenario) still passes unchanged because finance still maps
to `finance_head`.

## Known pre-existing issue (not caused by this, not fixed here)

`hr_head_node` / `legal_head_node` / `export_head_node` read required keys out of
`department_fields` with no fallback to `raw_input`, so a bare free-text
submission to those departments raises `TypeError` → HTTP 500 (see the † note
above). The backend department is unaffected.
