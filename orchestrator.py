"""The orchestrator: picks one department for a free-text request, or sends it to a human.

Order of attempts:
1. Jev (TypeSafe), a Choice question over the five departments. Used only if its
   own confidence is at least ``ROUTER_MIN_CONFIDENCE`` (default 0.75).
2. Claude Haiku on Anthropic's API, asked for exactly one department name. It may answer
   ``human_escalation`` if it cannot tell.
3. Otherwise the request goes to the human-review node, with a reason that says
   whether the classifiers were *unsure* (``request_not_classified``) or
   *unreachable* (``classifier_unavailable``).

Both classifiers run through lazily created clients with timeouts and retries
(``clients.py``). Nothing here raises for a classifier outage.

Routing is not cached: a cache only helps if looking up is cheaper than asking, and
a wrong cached route sends a request to the wrong department.
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

from typesafe_sdk import Choice

from clients import ROUTER_MODEL_ID, get_anthropic_client, get_typesafe_client
from state import GraphState, audit_entry

log = logging.getLogger(__name__)

DEPARTMENTS = [
    "finance",
    "hr_complaints",
    "backend_ticketing",
    "export_compliance",
    "legal_compliance",
]
HUMAN = "human_escalation"


def min_confidence() -> float:
    try:
        return float(os.environ["ROUTER_MIN_CONFIDENCE"])
    except (KeyError, ValueError):
        return 0.75


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



def local_classify(text: str) -> tuple[Optional[str], float]:
    response = get_typesafe_client().system_one(
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


def llm_classify(text: str, budget: int) -> tuple[Optional[str], str, int]:
    """Returns (department or None, outcome, budget_remaining). Never touches state.

    outcome is one of: "ok", "unsure" (the model declined or gave a non-department),
    "no_budget", "error".
    """
    if budget <= 0:
        return None, "no_budget", budget
    budget -= 1  # spent before the attempt: a failed call still counts
    try:
        response = get_anthropic_client().messages.create(
            model=ROUTER_MODEL_ID,
            max_tokens=20,
            system=ORCHESTRATOR_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        department = response.content[0].text.strip().lower()
    except Exception as exc:
        log.warning("LLM router unavailable: %s", type(exc).__name__)
        return None, "error", budget
    if department in DEPARTMENTS:
        return department, "ok", budget
    return None, "unsure", budget


@dataclass(frozen=True)
class Routing:
    department: Optional[str]
    source: str
    reason: Optional[str]
    budget: int


def route(text: str, budget: int) -> Routing:
    errored = False
    try:
        department, confidence = local_classify(text)
        if department in DEPARTMENTS and confidence >= min_confidence():
            return Routing(department, "local_model", None, budget)
    except Exception as exc:  # TypeSafe down, key missing, malformed reply...
        log.warning("Jev router unavailable: %s", type(exc).__name__)
        errored = True

    department, outcome, budget = llm_classify(text, budget)
    if department:
        return Routing(department, "llm", None, budget)
    errored = errored or outcome == "error"
    reason = "classifier_unavailable" if errored else "request_not_classified"
    return Routing(None, "none", reason, budget)


def orchestrator_node(state: GraphState) -> dict:
    routing = route(state["raw_input"], state["llm_budget_remaining"])
    update: dict = {
        "llm_budget_remaining": routing.budget,
        "request_type": routing.department or HUMAN,
        "audit_trail": [
            audit_entry(
                state,
                "orchestrator",
                "routed" if routing.department else "unrouted",
                department=routing.department,
                source=routing.source,
                reason=routing.reason,
            )
        ],
    }
    if routing.reason:
        update["escalation_reason"] = routing.reason
    return update
