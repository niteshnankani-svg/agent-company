# External services

The Python API talks to five HTTP services. None is an "MCP server": the code calls them with plain
HTTP (`services.py`), and the client modules are named `*_client.py`. Every address and timeout comes
from the environment (`.env.example`); there are no built-in addresses. If an address is unset, that
department returns `failed` with `not_configured`, and `/ready` shows it as unavailable.

What a person sees when a call fails is a short code only (for example `legal_rag: timeout (no response
within 60s)` or `http_status (HTTP 502)`). The remote service's response body and exception text are
written to the server log and never returned.

| Department | Client | Address | Timeout (default) | Reply used |
|---|---|---|---|---|
| Legal | `legal_client.py` | `LEGAL_QUERY_URL` | `LEGAL_TIMEOUT_SECONDS` (60) | `{answer, citations[{act, section_number, title, summary, score}]}` |
| HR | `hr_client.py` | `HR_AGENT_QUERY_URL` | `HR_AGENT_TIMEOUT_SECONDS` (60) | `{answer, evidence}` |
| Backend | `ticketing_client.py` | `TICKETING_URL` | `TICKETING_TIMEOUT_SECONDS` (30) | `{status, category, answer, needs_human, escalation_reason}` |
| Export (proposal) | `niryat_client.py` | `NIRYAT_CHAT_URL` | `NIRYAT_CHAT_TIMEOUT_SECONDS` (60) | server-sent events of `{chunk}`; an HS code is extracted from the prose |
| Export (check) | `niryat_client.py` | `NIRYAT_TRADEDATA_URL` | `NIRYAT_TRADEDATA_TIMEOUT_SECONDS` (90) | `{data[], intel}`; `found` is added here |

Finance has no external service. Routing, field extraction and verification use TypeSafe and
Anthropic's API (`ANTHROPIC_API_KEY`; see `.env.example`).

## What each service does and does not give us

- **Legal** returns each retrieved section's title and a short summary, not the raw statute text. The
  verifier can only check the answer against that. If the service also returned the retrieved passage
  text, checking would be much stricter and more useful.
- **HR** is a separate project (Text-to-SQL and RAG over employee data). It now uses a hosted model. This
  repo has not seen its real reply; the `evidence` field is assumed from its documentation. If it returns
  an answer without evidence, the answer is held for a person.
- **Backend** returns an answer and a category but not its sources, so its answers cannot be checked and
  are labelled "not independently checked".
- **Export** is a model's proposal plus a TradeStat lookup. A TradeStat record existing for a code does
  not prove the code fits the product; the verifier checks that the product matches the record's text.

## Readiness

`GET /ready` probes each address with a short request. Any HTTP reply means "reachable"; a connection
failure or timeout means "unreachable"; 5xx means "unhealthy". It reports status words only, never
addresses.
