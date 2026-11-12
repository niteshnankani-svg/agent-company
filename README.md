# Agent Company

You type one plain sentence. The system works out which of five departments should handle it, does the
work, checks the answer against its evidence, and either answers, asks you for missing details, or hands
the request to a person. You never choose a department.

It is a [LangGraph](https://langchain-ai.github.io/langgraph/) service (Python, FastAPI) with a small
Next.js console in front of it. This README describes the code as it stands. For *why* each choice was made,
and what breaks at 100x load, read [`DECISIONS.md`](DECISIONS.md). For a walkthrough you can explain to someone
else, read [`HOW_IT_WORKS.md`](HOW_IT_WORKS.md).

## What a request does

```
sentence ─► route to a department ─► (reuse a verified answer?) ─► department does its work
                                                                          │
              ┌───────────────────────────────────────────────────────────┤
              ▼                                                           ▼
   needs more details ──► a form is shown          verifier checks each claim against the evidence
   (ends here)                                        │ ok          │ unsupported        │ cannot check
                                                      ▼             ▼ (one retry,        ▼
                                                   answer       with feedback)      a person decides
                                                                    │ still unsupported       ▲
                                                                    └─────────────────────────┘
```

Every request ends in exactly one of four **statuses**:

| Status | Meaning |
|---|---|
| `answered` | There is an answer **and the evidence it relied on**. |
| `needs_human` | A person must decide. The run **pauses** until they do, and survives a restart. |
| `needs_more_info` | Details are missing. The reply lists exactly which, and the console shows a form. |
| `failed` | A service it depends on could not be used. The reply says which and why, with nothing internal in it. |

Each result also says how it was checked: `verified` (every claim supported by the evidence), `unchecked`
(nobody could check it, and the UI says so), or decided by a person.

## The five departments

| Department | What it does | Needs | Evidence | How it is checked |
|---|---|---|---|---|
| **Finance** | Customer credit (AR) or supplier invoice (AP) triage. Never decides: a person approves every valid request. | No external service. A model reads numbers from your sentence; a form asks for the rest. | The record, the policy outcome, and where each value came from | Rule-based, so not sent to a model |
| **HR** | Complaints always go to a person. Other questions are answered from employee data. | HR agent service | What the agent returns | Claims checked against it |
| **Backend** | IT tickets. A local screen catches injection attempts and security incidents first. | Ticketing agent service | Its classification only (it returns no sources) | **Cannot be checked**, labelled `unchecked` |
| **Export** | Proposes an HS code, then checks it exists in India's TradeStat records. | NiryatAI service | The proposal and the TradeStat record | Verifier confirms the record matches your product |
| **Legal** | Indian statutes (BNS, BNSS, BSA, DPDP). | Legal RAG service | Each retrieved section's title and summary | Claims checked against them |

Service addresses and timeouts are in [`docs/SERVICES.md`](docs/SERVICES.md).

## Run it locally

You need Python 3.11+, Node 22+, and, for real routing, a TypeSafe key and AWS credentials for Bedrock.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit: service addresses, TYPESAFE_API_KEY, API_KEYS
set -a && . ./.env && set +a
uvicorn api:app --port 8080
```

Without service addresses a department returns `failed: not_configured`, and without the router key every request
goes to a person with the reason `classifier_unavailable`. Both are clear, nothing crashes. `GET /ready` shows
what is and is not set up.

For local development only, `AGENT_AUTH_DISABLED=true` switches API-key authentication off.

The console:

```bash
cd frontend
npm install
cp .env.local.example .env.local   # AGENT_COMPANY_API_URL and AGENT_COMPANY_API_KEY (a "client" key)
npm run dev                        # http://localhost:3000
```

## The API

Authentication is `Authorization: Bearer <key>`. Keys are set with `API_KEYS=name:role:key,...`
(see `.env.example`). Two roles: **client** (submit, look up a run) and **reviewer** (also list and decide reviews).
With no keys configured the API answers 503 rather than letting everyone in.

| Endpoint | Role | What it does |
|---|---|---|
| `POST /request` | client | `{request_id, raw_input}` → a result. Also accepts `follow_up` when answering a form it generated. |
| `GET /runs/{run_id}` | client | Where a run stands: the result so far and whether it is `pending_review`. |
| `GET /reviews` | reviewer | Runs waiting for a person, oldest first. |
| `POST /reviews/{run_id}/resume` | reviewer | `{action: approve \| answer \| reject, note?, answer?}`. The reviewer recorded is the **name of the key used**. |
| `GET /ready` | open | Storage, auth, and each department's service: ready, degraded, or not ready. |
| `GET /health` | open | The process is up. |

```bash
curl -s localhost:8080/request -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"request_id":"demo-1","raw_input":"What is Section 357 of BNS about?"}'
```

The reply carries `run_id`, `result` (`status`, `answer`, `evidence`, `confidence`, `missing_fields`, `follow_up`,
`error`, `reason`), `verification`, `pending_review`, and an `audit_trail` of every step the graph took.
`confidence` is `{kind, value}` and is only a number when the department has a real one (Legal's retrieval
score, labelled as such). Everywhere else it is `none`.

## When a person is needed

Anything the system cannot or should not decide pauses at a review node. Reviewers use the API above:
`GET /reviews` to see the queue, then `POST /reviews/{id}/resume` to **approve** the draft, **answer** themselves, or
**reject** with a note. Two reviewers racing on one run cannot both win. The paused run is stored in SQLite
(`CHECKPOINT_DB_PATH`) or Postgres (`CHECKPOINT_DB_URL`) and is still there after a restart. The person who asked sees a
reference and a "check for a decision" button.

There is no reviewer screen: reviewing is done through the API.

## Deploy

```bash
docker build -t agent-company .
docker run -p 8080:8080 -v agent-data:/app/data --env-file .env agent-company
```

The image holds only what the API imports, runs as a non-root user, keeps the SQLite file in `/app/data`, and has a
health check. For several workers or machines use Postgres (`pip install -r requirements-postgres.txt`, set
`CHECKPOINT_DB_URL`) and note that the answer cache is per process. The database stores each request and its
evidence, so restrict access to it and decide how long to keep it.

CI builds the image and starts it (see `.github/workflows/ci.yml`). It has not been built by the author of this
README: there was no Docker daemon available, so the first CI run is its first real test.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q                 # no keys, no network: every external service is mocked
```

24 tests need a real Postgres and are skipped unless `TEST_POSTGRES_URL` is set:

```bash
pip install -r requirements-postgres.txt
TEST_POSTGRES_URL=postgresql://user:pass@localhost:5432/agentco python -m pytest -q
```

The console's browser tests run against a mock of the API and need no backend or keys:
`cd frontend && npm run test:e2e` (first time: `npx playwright install chromium`).

GitHub Actions runs all of the above on every pull request, using a Postgres service container.

### The live eval (you run this yourself)

```bash
python evals/run_evals.py --api-url https://your-api.example --api-key "$EVAL_API_KEY"
python evals/run_evals.py --dry-run          # list the cases and the coverage matrix, call nothing
```

20 cases cover every department's `answered`, `needs_human`, `needs_more_info` and `failed` paths (cells that cannot
happen by design are listed with the reason). Pass or fail is decided by deterministic checks; an optional LLM judge only
comments. For the "service down" cases it starts a local copy of the API with those addresses deliberately broken.
Details: [`evals/README.md`](evals/README.md). No eval has been run against real services in building this repository.

## Layout

| File | Role |
|---|---|
| `api.py`, `auth.py`, `ready.py` | HTTP endpoints, API keys, readiness |
| `graph.py`, `state.py` | The graph and its state |
| `orchestrator.py`, `clients.py` | Routing and its model clients (created on first use, with timeouts and retries) |
| `contract.py` | The one result shape every department returns |
| `departments.py` | The five departments behind one call, with a hard deadline |
| `finance.py`, `finance_input.py`, `finance_policy.py`, `extraction.py` | Finance, and reading values from a sentence safely |
| `hr_complaints.py`, `backend_ticketing.py`, `export_compliance.py`, `legal_compliance.py` | The other departments |
| `hr_client.py`, `ticketing_client.py`, `niryat_client.py`, `legal_client.py`, `services.py` | Calling the services |
| `verifier.py` | Checks claims against evidence; one retry with feedback |
| `cache.py` | Semantic answer cache with a policy for what may never be cached (off until `EMBEDDING_PROVIDER` is set) |
| `review.py`, `review_store.py`, `runtime.py` | Human decisions, the review inbox, and durable storage |
| `frontend/` | The console |
| `tests/`, `evals/` | Tests (offline) and the live eval (yours to run) |
| `train_ap_classifier.py`, `synthetic_data_generator.py`, `models/`, `syntheic_data/` | Offline training material for a finance model that is **not used** by the running system. Left in place; see `DECISIONS.md` |

## What is not true yet

- **Nothing here has run against your real services.** Every service is mocked in the tests. Your first real run is the
  real test; the live eval is how to do it.
- **Legal answers will often go to a person at first.** The Legal service returns only section titles and summaries, so the
  verifier cannot confirm detail that appears only in the full text.
- **Backend answers are not independently checked**, because that service returns no sources.
- **The HR agent's reply shape is assumed** (`{answer, evidence}`); an answer with no evidence is held for a person.
- **The 0.95 cache similarity threshold is a placeholder** until measured with a real embedding model.
- **No rate limiting** on the API, **no cleanup** of old stored runs, and **no reviewer screen**.
- **The Docker image has not been built by the author** (see above).
