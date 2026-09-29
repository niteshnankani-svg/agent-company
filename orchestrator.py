from typesafe_sdk import Choice, TypeSafeClient

try:
    from cache import DepartmentCache
    from state import GraphState, log_audit
except ImportError:
    from .cache import DepartmentCache
    from .state import GraphState, log_audit


orchestrator_cache = DepartmentCache(
    department="orchestrator", similarity_threshold=0.90
)

DEPARTMENTS = [
    "finance",
    "hr_complaints",
    "backend_ticketing",
    "export_compliance",
    "legal_compliance",
]

typesafe_client = TypeSafeClient()

# Descriptions derived from the department definitions this router used
# before Jev (see git history for the original keyword lists).
_DEPARTMENT_CRITERIA = {
    "finance": (
        "Invoices, payments, credit, loans, supplier/vendor payables, refunds, "
        "charges, billing, reimbursement, ledger entries, accounts receivable "
        "or payable, purchase orders"
    ),
    "hr_complaints": (
        "Employee complaints, grievances, harassment, payroll, onboarding, "
        "hiring, benefits, leave requests, manager/coworker/colleague "
        "workplace issues, discrimination, bullying"
    ),
    "export_compliance": (
        "HS codes, exports, shipments, customs, tariffs, imports, freight, "
        "shipping, bill of lading, incoterms, duty drawback"
    ),
    "legal_compliance": (
        "Statutes (BNS, BNSS, BSA, DPDP), legal sections, contracts, "
        "compliance, regulation, NDAs, liability, lawsuits, clauses, "
        "counsel, law/legal questions"
    ),
    "backend_ticketing": (
        "IT and technical issues: errors, bugs, tickets, system outages, "
        "crashes, servers, timeouts, login/sign-in problems, uploads, "
        "databases, APIs, VPN, passwords, hardware (laptop, desktop, "
        "printer, keyboard, monitor), software, network, wifi, routers, "
        "firewalls, malware, phishing, access, applications, authentication, "
        "browsers, data breaches"
    ),
}


def local_classify(text: str) -> tuple[str | None, float]:
    # TypeSafe's Jev model classifies via a Choice question over the same
    # five departments — same function signature as before; nothing
    # downstream needs to change.
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


ORCHESTRATOR_PROMPT = """Respond with EXACTLY ONE department name from this list:
finance, hr_complaints, backend_ticketing, export_compliance, legal_compliance
Respond with nothing else: no punctuation and no explanation. If the request
spans multiple departments, pick the one owning the primary decision. If you
cannot confidently classify it, respond exactly: human_escalation."""


def llm_classify(text: str, state: GraphState) -> tuple[str, float]:
    if state["llm_budget_remaining"] <= 0:
        return "human_escalation", 0.0

    try:
        from anthropic import AnthropicBedrock

        client = AnthropicBedrock(aws_region="us-east-1")
        # Decremented before the attempt — a failed call still counts.
        state["llm_budget_remaining"] -= 1
        response = client.messages.create(
            model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            max_tokens=100,
            system=ORCHESTRATOR_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        department = response.content[0].text.strip().lower()
        if department in DEPARTMENTS:
            return department, 0.85
        return "human_escalation", 0.85
    except Exception as exc:
        print(f"Anthropic call skipped/failed: {exc}")
        return "human_escalation", 0.0


def orchestrator_node(state: GraphState) -> GraphState:
    cached = orchestrator_cache.get(state["raw_input"])
    if cached:
        state["request_type"] = cached["department"]
        log_audit(state, "orchestrator", cached["department"], source="cache")
        return state

    department, confidence = local_classify(state["raw_input"])
    if department is not None and confidence >= 0.75:
        state["request_type"] = department
        log_audit(state, "orchestrator", department, source="local_model")
        orchestrator_cache.set(state["raw_input"], {"department": department})
        return state

    department, confidence = llm_classify(state["raw_input"], state)
    state["request_type"] = department
    log_audit(
        state,
        "orchestrator",
        department,
        source="llm" if confidence > 0 else "human_escalation",
    )
    return state
