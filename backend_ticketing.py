"""Backend / IT-ticketing department.

Local screens run first, on the ticket text itself, before any service sees it:
input validation, PII scrubbing, a prompt-injection check and a security-keyword
check. A ticket that trips a screen goes to a human and the ticketing agent is
never called. Everything else is sent (scrubbed) to the deployed ticketing agent
at ``TICKETING_URL``, which classifies and, when it is sure, answers.

The agent returns a category, an answer and its own ``needs_human`` verdict. It
does not return the sources behind its answer, so the only evidence available is
the agent's classification. Its confidence number has no known meaning (the old
docs call it a fixed proxy), so confidence is ``none`` and the raw value is kept
in evidence for the reviewer.
"""
import asyncio
import re

from contract import DepartmentResult, Evidence, service_error
from pii import scrub_pii
from ticketing_client import ask_ticketing_agent

DEPARTMENT = "backend_ticketing"
MAX_TICKET_CHARS = 8000

_INJECTION = re.compile(
    r"ignore\s+(all\s+)?(previous|prior|system)|system\s*prompt|reveal\s+.*secret|bypass\s+.*(auth|guard)",
    re.I,
)
_SECURITY = re.compile(
    r"\b(malware|ransomware|phishing|breach|botnet|ddos|intrusion|keylogger)\b"
    r"|compromised\s+credentials|unauthorized\s+access|suspicious\s+|brute\s+force",
    re.I,
)


def run_backend(raw_input: str) -> DepartmentResult:
    valid = (
        isinstance(raw_input, str)
        and bool(raw_input.strip())
        and len(raw_input) <= MAX_TICKET_CHARS
        and "\x00" not in raw_input
    )
    if not valid:
        return DepartmentResult(
            department=DEPARTMENT,
            status="failed",
            error=(
                "invalid_ticket_input: text must be non-empty, at most "
                f"{MAX_TICKET_CHARS} characters, and contain no NUL bytes"
            ),
        )

    text = scrub_pii(raw_input.strip())

    injection = _INJECTION.search(text)
    if injection:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="prompt_injection_suspected",
            evidence=[Evidence(source="local_screen:injection", text=injection.group(0))],
        )

    security = _SECURITY.search(text)
    if security:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="security_incident_review",
            evidence=[Evidence(source="local_screen:security_keywords", text=security.group(0))],
        )

    reply = asyncio.run(ask_ticketing_agent(text))
    if "error" in reply:
        return DepartmentResult(
            department=DEPARTMENT,
            status="failed",
            error=service_error("ticketing_agent", reply),
        )

    category = reply.get("category") or reply.get("predicted_category")
    agent_facts = {
        k: reply[k]
        for k in ("status", "category", "predicted_category", "confidence", "escalation_reason", "reason")
        if k in reply
    }

    if reply.get("needs_human") or reply.get("status") == "needs_human_review":
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason=reply.get("escalation_reason") or reply.get("reason") or "ticketing_agent_escalated",
            evidence=[Evidence(source="ticketing_agent", data=agent_facts or {"status": "needs_human_review"})],
        )

    answer = reply.get("answer") or ""
    if not answer.strip():
        return DepartmentResult(
            department=DEPARTMENT, status="failed", error="ticketing_agent: empty answer"
        )
    return DepartmentResult(
        department=DEPARTMENT,
        status="answered",
        answer=answer,
        evidence=[
            Evidence(
                source="ticketing_agent",
                text=f"Classified by the ticketing agent as: {category}" if category else None,
                data=agent_facts or {"status": "answered"},
            )
        ],
    )
