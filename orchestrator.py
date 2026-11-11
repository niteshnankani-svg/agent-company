from typesafe_sdk import Choice, TypeSafeClient

from cache import DepartmentCache
from state import GraphState, audit_entry

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

typesafe_client = TypeSafeClient()  # made lazy in Phase 2

# Descriptions derived from the department definitions this router used
# before Jev (see git history for the original keyword lists).
_DEPARTMENT_CRITERIA = {
    "finance": (
        "Invoices, payments, credit, loans, supplier/vendor payables, refunds, "
        "charges, billing, reimbursement, ledger entries, accounts receivable "
        "or payable, purchase orders"
    ),
    "hr_complaints": (
        "Employee/HR matters: complaints, grievances, harassment, "
        "discrimination, bullying, workplace conflicts (always escalated to "
        "a human) as well as general informational questions about "
        "employees or HR data - headcounts, department stats, payroll, "
        "onboarding, hiring, benefits, leave requests (answered directly "
        "when possible)"
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
    # five departments.
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


def llm_classify(text: str, budget: int) -> tuple[str, float, int]:
    """Returns (department, confidence, budget_remaining). Does not touch state."""
    if budget <= 0:
        return "human_escalation", 0.0, budget

    try:
        from anthropic import AnthropicBedrock

        client = AnthropicBedrock(aws_region="us-east-1")
        # Spent before the attempt: a failed call still counts.
        budget -= 1
        response = client.messages.create(
            model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            max_tokens=100,
            system=ORCHESTRATOR_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        department = response.content[0].text.strip().lower()
        if department in DEPARTMENTS:
            return department, 0.85, budget
        return "human_escalation", 0.85, budget
    except Exception as exc:
        print(f"Anthropic call skipped/failed: {exc}")
        return "human_escalation", 0.0, budget


def orchestrator_node(state: GraphState) -> dict:
    cached = orchestrator_cache.get(state["raw_input"])
    if cached:
        return {
            "request_type": cached["department"],
            "audit_trail": [
                audit_entry(state, "orchestrator", "routed", department=cached["department"], source="cache")
            ],
        }

    department, confidence = local_classify(state["raw_input"])
    if department is not None and confidence >= 0.75:
        orchestrator_cache.set(state["raw_input"], {"department": department})
        return {
            "request_type": department,
            "audit_trail": [
                audit_entry(state, "orchestrator", "routed", department=department, source="local_model")
            ],
        }

    department, confidence, budget = llm_classify(
        state["raw_input"], state["llm_budget_remaining"]
    )
    return {
        "request_type": department,
        "llm_budget_remaining": budget,
        "audit_trail": [
            audit_entry(
                state,
                "orchestrator",
                "routed",
                department=department,
                source="llm" if confidence > 0 else "unclassified",
            )
        ],
    }
