"""HR department.

Two jobs, in this order:

1. **Complaint gate.** TypeSafe's Jev model is asked whether the text is a specific
   employee complaint or grievance. If so, the request is ``needs_human``. This
   system never tries to resolve complaints.
2. **Informational questions.** Anything else is sent to the HR agent (a separate
   Text-to-SQL + RAG service over employee data). The agent's answer is returned
   with the evidence the agent supplied. If the agent supplies no evidence the
   result is ``needs_human``: an HR answer nobody can check is not released.

Confidence is ``none`` everywhere here. Jev's complaint probability is a routing
signal, not a measure of whether an answer is right, so it is recorded as
evidence and never presented as confidence.
"""
import asyncio
from typing import Any, Optional

from typesafe_sdk import Noul

from clients import get_typesafe_client
from contract import DepartmentResult, Evidence, service_error, with_feedback
from hr_client import ask_hr_agent

DEPARTMENT = "hr_complaints"
COMPLAINT_THRESHOLD = 0.5


def complaint_probability(text: str) -> float:
    response = get_typesafe_client().system_one(
        state=text,
        questions={
            "is_complaint": Noul(
                instructions=(
                    "Is this a specific employee complaint or grievance "
                    "(harassment, workplace conflict, discrimination, "
                    "bullying, an issue with a specific person or incident) "
                    "that needs human review, as opposed to a general "
                    "informational question about HR or employee data?"
                ),
            ),
        },
    )
    return response.nouls["is_complaint"].noul


def _evidence_from_agent(raw: Any) -> list[Evidence]:
    """Normalise whatever evidence shape the HR agent returned into Evidence items."""
    source = "hr_agent:sql_rag"
    if not raw:
        return []
    if isinstance(raw, str):
        return [Evidence(source=source, text=raw)] if raw.strip() else []
    if isinstance(raw, dict):
        return [Evidence(source=source, data=raw)]
    if isinstance(raw, list):
        return [ev for item in raw for ev in _evidence_from_agent(item)]
    return [Evidence(source=source, text=str(raw))]


def run_hr(raw_input: str, fields: Optional[dict], feedback: Optional[str] = None) -> DepartmentResult:
    text = (fields or {}).get("complaint_text") or raw_input

    try:
        p = complaint_probability(text)
    except Exception as exc:  # the gate is a network call; a failure is a result, not a crash
        return DepartmentResult(
            department=DEPARTMENT,
            status="failed",
            error=f"complaint_gate_unavailable: {type(exc).__name__}",
        )

    if p >= COMPLAINT_THRESHOLD:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="employee_complaint",
            evidence=[
                Evidence(
                    source="jev_complaint_gate",
                    data={"complaint_probability": p, "threshold": COMPLAINT_THRESHOLD},
                )
            ],
        )

    # The complaint gate above saw the original text only; feedback goes to the HR agent alone.
    result = asyncio.run(ask_hr_agent(with_feedback(text, feedback)))
    if "error" in result:
        return DepartmentResult(
            department=DEPARTMENT,
            status="failed",
            error=service_error("hr_agent", result),
        )

    answer = result.get("answer") or ""
    evidence = _evidence_from_agent(result.get("evidence"))
    if not answer.strip():
        return DepartmentResult(
            department=DEPARTMENT, status="failed", error="hr_agent: empty answer"
        )
    if not evidence:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="hr_agent_returned_no_evidence",
            answer=answer,
        )
    return DepartmentResult(department=DEPARTMENT, status="answered", answer=answer, evidence=evidence)
