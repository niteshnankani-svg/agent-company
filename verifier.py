"""Verifier: INTERIM, structural checks only, until Phase 4.

What this does today: confirm the department produced a result that satisfies the
``DepartmentResult`` contract. A missing or malformed result fails (and the graph
retries, then hands to a human). Any valid result passes, whatever its status.

What it deliberately does NOT do: judge whether an answer is true. The old
confidence-threshold and LLM "consistency" check are gone because the numbers they
read are gone (confidence is now ``none`` for most departments, and a threshold on
a retrieval score proves nothing). Phase 4 replaces this with a check that each
claim in the answer is supported by the evidence text.
"""
from pydantic import ValidationError

from contract import DepartmentResult
from state import GraphState, audit_entry


def verifier_node(state: GraphState) -> dict:
    raw = state.get("department_result")
    problem = None
    if raw is None:
        problem = "no result was produced"
    else:
        try:
            DepartmentResult.from_state(raw)
        except ValidationError:
            problem = "result violates the DepartmentResult contract"

    if problem:
        return {
            "verifier_result": "fail",
            "retry_count": state["retry_count"] + 1,
            "audit_trail": [audit_entry(state, "verifier", "fail", note=problem)],
        }
    return {
        "verifier_result": "pass",
        "audit_trail": [audit_entry(state, "verifier", "pass", check="structure_only")],
    }
