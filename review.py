"""What a human reviewer can decide, and what each decision turns the result into.

A request that a machine could not finish (router could not decide, a department said a person
must decide, the verifier withdrew an answer) pauses at the ``human_review`` node. A person then
sends one of three decisions:

* ``approve``: accept the draft answer as it stands. Only possible if there is a draft.
* ``answer``: write the answer themselves.
* ``reject``: decline the request, with a note.

The final result always records who decided. A human decision is NOT a machine verification, so the
result's verification is ``human_approved`` / ``human_provided`` and is never cached.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contract import DepartmentResult, Evidence

Action = Literal["approve", "answer", "reject"]
ACTIONS: tuple[str, ...] = ("approve", "answer", "reject")


class HumanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    reviewer: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(default=None, max_length=2000)
    answer: Optional[str] = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def action_needs(self) -> "HumanDecision":
        if self.action == "answer" and not (self.answer and self.answer.strip()):
            raise ValueError("action 'answer' needs the answer text")
        if self.action != "answer" and self.answer:
            raise ValueError("'answer' text is only valid with action 'answer'")
        return self


def has_draft(held: DepartmentResult) -> bool:
    return bool(held.answer and held.answer.strip())


def review_payload(run_id: str, held: DepartmentResult, question: str) -> dict:
    """What the paused run shows a reviewer. Plain JSON, so it is stored in the checkpoint."""
    return {
        "run_id": run_id,
        "department": held.department,
        "reason": held.reason,
        "question": question,
        "draft_answer": held.answer,
        "evidence_sources": [e.source for e in held.evidence],
        "allowed_actions": [a for a in ACTIONS if a != "approve" or has_draft(held)],
    }


def apply_decision(held: DepartmentResult, decision: HumanDecision) -> tuple[DepartmentResult, dict]:
    """(final result, verification) for a reviewer's decision on a held result."""
    who = {"decision": decision.action, "reviewer": decision.reviewer}
    if decision.note:
        who["note"] = decision.note
    record = Evidence(source="human_review", text=decision.note or None, data=who)

    if decision.action == "approve":
        if not has_draft(held):
            raise ValueError("there is no draft answer to approve")
        final = DepartmentResult(
            department=held.department, status="answered", answer=held.answer,
            evidence=[*held.evidence, record], confidence=held.confidence,
        )
        status = "human_approved"
    elif decision.action == "answer":
        final = DepartmentResult(
            department=held.department, status="answered", answer=decision.answer, evidence=[record],
        )
        status = "human_provided"
    else:
        final = DepartmentResult(
            department=held.department, status="failed", reason="declined_by_reviewer",
            error=f"Declined by a reviewer: {decision.note or 'no reason given'}",
            evidence=[record],
        )
        status = "not_applicable"
    return final, {"status": status, "check": "human_review", "reviewer": decision.reviewer}
