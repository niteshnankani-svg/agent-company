# MCP servers, per department

This is a reference for what exists today: which of the five LangGraph
departments has an MCP-wrapped external agent behind it, what real service
each one calls, and the actual code. Written after wiring up the last two
(Backend/Ticketing, HR) in this session — see `git log` on each file for the
exact commits.

## Orchestrator (routing, not a department)

Not an MCP server — `orchestrator.py`'s `local_classify()` calls TypeSafe's
Jev model directly (a `Choice` question over the five department names) to
decide which department handles a ticket. Falls back to Claude via Bedrock
(`llm_classify()`) if Jev's confidence is low. This routing decision runs
before any of the five nodes below.

```python
# orchestrator.py (excerpt)
def local_classify(text: str) -> tuple[str | None, float]:
    response = typesafe_client.system_one(
        state=text,
        questions={
            "department": Choice(
                instructions="Which department should handle this request?",
                criteria=_DEPARTMENT_CRITERIA,
            ),
        },
    )
    answer = response.choices["department"]
    return answer.choice, answer.confidence
```

---

## 1. Finance — no MCP server (by design)

`finance.py` never calls an external agent. Every finance decision - both
`customer_side_finance` (credit/AR) and `supplier_side_finance` (AP) - always
returns `human_escalation`, regardless of the underlying `credit_review`/
`invoice_policy` analysis. This is deliberate "fail-closed model governance"
(see the module's own docstring): a legacy XGBoost credit model exists but is
quarantined, evaluated offline only, and never makes a live decision here.

```python
# finance.py (excerpt)
def customer_side_finance(customer_fields):
    try: analysis=credit_review(customer_fields)
    except ValueError:
        analysis={'reason':'invalid_credit_record'}
    return {'decision':'human_escalation','confidence':0.0,
            'rationale':'Credit model quarantined: labeled default evaluation and provenance required.',
            'evidence_ids':[],'source':'human_escalation','analysis':analysis}
```

There is nothing to wrap here - the current Vercel deployment's
always-escalate behavior for finance is already faithful to this.

---

## 2. Export/Compliance → `niryat_mcp.py`

Wraps the **NiryatAI** backend (deployed on Railway,
`https://backend-production-a3c6.up.railway.app`) - a public HTTP service,
not local. Two tools, two different evidence qualities: `ask_export_question`
is an LLM proposal (Claude's prose, with an HS code regex-extracted out of
it); `get_trade_data` is real Indian government TradeStat data used to
verify that proposal.

```python
# niryat_mcp.py
NIRYAT_CHAT_URL = "https://backend-production-a3c6.up.railway.app/chat"
NIRYAT_TRADEDATA_URL = "https://backend-production-a3c6.up.railway.app/tradedata"

mcp = FastMCP("niryat-ai")

@mcp.tool()
async def ask_export_question(message: str) -> dict:
    """PROPOSAL only - cross-check with get_trade_data before trusting the code.
    Returns: {full_answer, hs_code (or None), extraction_ok (bool)}."""
    # streams /chat's SSE response, regex-extracts an HS code out of the prose
    ...

@mcp.tool()
async def get_trade_data(hs_code: str, trade_type: str = "export", year: str = "2023-2024") -> dict:
    """REAL government commodity record + market intel.
    Returns: {data (list), intel (dict), found (bool)}."""
    ...
```

Called from `export_compliance.py`'s `export_head_node`: proposes an HS code,
verifies it against TradeStat, and sets confidence based on whether the code
was actually found in government records (0.85 verified, 0.4 unverified).

**Verified live**: cotton t-shirts → HS code `610910`, confirmed against real
TradeStat commodity data, confidence 0.85, not escalated.

---

## 3. Legal/Compliance → `legal_rag_mcp.py`

Wraps a **local** FastAPI service (`legal-rag-chatbot`, a separate project at
`/Users/apple/Projects/legal-rag-chatbot`) - BERT retrieval over an indexed
corpus of BNS/BNSS/BSA/DPDP Act 2023 text, plus Claude/GPT-4o generation.
Started with `uvicorn app.api.main:app --port 8000`; needs Redis (already
installed natively, `redis-server`) and a pre-built embedding index (already
present under `data/index/`).

```python
# legal_rag_mcp.py (full file)
LEGAL_QUERY_URL = "http://localhost:8000/query"
mcp = FastMCP("legal-rag")

@mcp.tool()
async def ask_legal_question(question: str, act_filter: str | None = None) -> dict:
    """Grounded in indexed legal text via BERT retrieval and LLM generation.
    Returns: {answer, citations (with similarity score + section number)}."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(LEGAL_QUERY_URL,
            json={"question": question, "act_filter": act_filter})
        response.raise_for_status()
        return response.json()
```

Called from `legal_compliance.py`'s `legal_head_node`: extracts the primary
section citation from the generated answer text (falls back to the
highest-similarity retrieved section if the answer doesn't cite one
explicitly), uses the top citation's raw similarity score as confidence
un-gated (the shared verifier owns the ambiguous-confidence decision).

**Verified live**: "What is Section 357 of BNS about?" → correctly cited
`BNS Section 357`, confidence 0.6425, not escalated, real generated
explanation grounded in the actual statute text.

---

## 4. Backend/Ticketing → `ticketing_mcp.py` *(added this session)*

Wraps the **already-deployed Multi_agent_ticketing_system backend** (EC2
behind CloudFront, `https://d26edylmd2q5pk.cloudfront.net`) - the sibling
project built earlier in this same overall effort. Unlike the other three,
this one *resolves* the ticket outright (not just classifies/verifies) when
confident: it runs its own internal classifier + RAG-grounded answer
generation and returns a real answer plus its own `needs_human` verdict.

```python
# ticketing_mcp.py (full file)
TICKETING_URL = "https://d26edylmd2q5pk.cloudfront.net/ticket"
mcp = FastMCP("ticketing-agent")

@mcp.tool()
async def ask_ticketing_agent(raw_text: str) -> dict:
    """Returns: {status, category, answer, needs_human, escalation_reason}
    on success ("answered" tickets have a real answer; "needs_human_review"
    tickets have a reason + predicted_category/confidence instead).
    On failure: dict with an "error" key."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(TICKETING_URL, json={"raw_text": raw_text})
        response.raise_for_status()
        return response.json()
```

Called from `backend_ticketing.py`'s `backend_head_node`, but only *after*
local input validation, PII scrubbing, and injection/security-keyword regex
screening - those run on the raw text regardless of which agent answers, and
still escalate locally without ever calling the agent. This replaced the
former local DistilBERT classifier (`predict_backend`/`backend_model.py`,
which depended on a sibling project's worker that was never actually wired
up in this environment) - a real capability change from "classify and route
to a human team" to "resolve confident tickets directly."

**Verified live**: cached ticket, fresh ticket, and a security-flagged
ticket (escalates locally, agent never called) - all correct, through the
full public chain.

---

## 5. HR/Complaints → `hr_agent_mcp.py` *(added this session)*

Wraps a new minimal HTTP server (`query_server.py`, stdlib-only
`http.server`, matching `hr-agent`'s own zero-dependency style) around the
**local** `hr-agent` project (`/Users/apple/Projects/hr-agent`) - Text-to-SQL
+ RAG over an employee CSV, via Ollama (`llama3.2` for planning/generation,
`embeddinggemma` for retrieval), started with
`python3 query_server.py --port 8090`.

```python
# hr_agent_mcp.py (full file)
HR_AGENT_QUERY_URL = "http://localhost:8090/query"
mcp = FastMCP("hr-agent")

@mcp.tool()
async def ask_hr_agent(question: str) -> dict:
    """Plans and runs a read-only SQL query grounded in retrieved records,
    then writes an answer. Returns: {answer, evidence} on success.
    On failure: dict with an "error" key."""
    async with httpx.AsyncClient(timeout=280.0) as client:
        response = await client.post(HR_AGENT_QUERY_URL, json={"question": question})
        response.raise_for_status()
        return response.json()
```

Called from `hr_complaints.py`'s `hr_head_node`, but only after a Jev `Noul`
question gates out actual complaints/grievances (those always escalate to a
human without ever reaching this agent - this system does not attempt to
resolve them). Note the unusually long 280s timeout: a fresh question chains
two local LLM calls and measured anywhere from ~20s to ~4 minutes depending
on how much CPU contention this machine's other local services create at the
time.

**Verified live**: complaint gate still escalates correctly; fresh questions
return real, grounded SQL-backed answers, including correctly answering "0"
for departments that don't actually exist in the data (Legal, Operations)
rather than hallucinating a number - confirmed against the real CSV.

---

## What's running locally, right now, for all of this to work

| Service | Port | Started with |
|---|---|---|
| `agent_company` backend | 8080 | `uvicorn api:app --host 0.0.0.0 --port 8080` |
| `legal-rag-chatbot` API | 8000 | `uvicorn app.api.main:app --host 0.0.0.0 --port 8000` |
| `hr-agent` query server | 8090 | `python3 query_server.py --port 8090` |
| Redis | 6379 | `redis-server` |
| Ollama | 11434 | `ollama serve` (models: `llama3.2`, `embeddinggemma`) |
| Public tunnel | - | `ngrok http 8080` → currently `https://imply-dolly-extending.ngrok-free.dev` |

The Vercel deployment (`agent-company-console`) proxies to the tunnel URL via
its `AGENT_COMPANY_API_URL` env var when set and reachable, falling back to
a simpler in-process TypeScript reimplementation otherwise (see
`frontend/app/api/agent/request/route.ts`). Export and Legal call public/
already-deployed services and don't depend on this machine staying on;
Backend depends on the separate EC2/CloudFront deployment staying up; HR
depends on this machine specifically (Ollama has no public equivalent
wired up).
