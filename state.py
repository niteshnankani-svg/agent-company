"""Graph state and the helpers nodes use to build *partial* updates.

Nodes never mutate the state they receive. They return a dict containing only
the keys they change; LangGraph merges it. List fields that several nodes add
to (``audit_trail``, ``department_history``) use an append reducer, so a node
returns just the new entries.
"""
import operator
from typing import Annotated, Any, Optional, TypedDict

from contract import DepartmentResult


class GraphState(TypedDict):
    request_id: str
    raw_input: str
    request_type: Optional[str]
    departments_activated: list[str]
    llm_budget_remaining: int
    # Caller-supplied multi-department run (kept until Phase 2 revisits it).
    # Set only by a follow-up form the system itself generated: skip routing, go to this department.
    pinned_department: Optional[str]
    follow_up: bool
    department_sequence: Optional[list[str]]
    department_fields_by_dept: Optional[dict]
    department_history: Annotated[list[dict], operator.add]
    active_department: Optional[str]
    department_fields: Optional[dict]
    sub_department: Optional[str]
    # A DepartmentResult as a plain dict (see contract.DepartmentResult.to_state).
    department_result: Optional[dict]
    verifier_result: Optional[str]
    cache_hit: bool
    retry_count: int
    escalation_reason: Optional[str]
    audit_trail: Annotated[list[dict], operator.add]


def new_state(
    request_id: str,
    raw_input: str,
    llm_budget: int = 5,
    department_fields: Optional[dict] = None,
    sub_department: Optional[str] = None,
    department_sequence: Optional[list[str]] = None,
    department_fields_by_dept: Optional[dict] = None,
    pinned_department: Optional[str] = None,
    follow_up: bool = False,
) -> GraphState:
    return {
        "request_id": request_id,
        "raw_input": raw_input,
        "request_type": None,
        "departments_activated": [],
        "llm_budget_remaining": llm_budget,
        "pinned_department": pinned_department,
        "follow_up": follow_up,
        "department_sequence": department_sequence,
        "department_fields_by_dept": department_fields_by_dept,
        "department_history": [],
        "active_department": None,
        "department_fields": department_fields,
        "sub_department": sub_department,
        "department_result": None,
        "verifier_result": None,
        "cache_hit": False,
        "retry_count": 0,
        "escalation_reason": None,
        "audit_trail": [],
    }


def audit_entry(state: GraphState, node: str, status: str, **extra: Any) -> dict:
    """One audit-trail record. Returned inside a partial update, never appended in place."""
    return {"node": node, "status": status, "request_id": state["request_id"], **extra}


def result_update(
    state: GraphState, node: str, result: DepartmentResult
) -> dict:
    """The partial update every department node returns for a finished result."""
    return {
        "department_result": result.to_state(),
        "active_department": result.department,
        "audit_trail": [
            audit_entry(
                state,
                node,
                result.status,
                department=result.department,
                reason=result.reason,
            )
        ],
    }
