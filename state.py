from typing import Literal, NotRequired, Optional, TypedDict


class DepartmentOutput(TypedDict):
    decision: str
    confidence: float
    rationale: str
    evidence_ids: list[str]
    source: Literal["cache", "local_model", "llm"]


class GraphState(TypedDict):
    backend_triage: NotRequired[dict]
    request_id: str
    raw_input: str
    request_type: Optional[str]
    departments_activated: list[str]
    llm_budget_remaining: int
    department_sequence: Optional[list[str]]
    department_history: Optional[list[dict]]
    department_fields_by_dept: Optional[dict]
    active_department: Optional[str]
    department_fields: Optional[dict]
    sub_department: Optional[str]
    department_output: Optional[DepartmentOutput]
    verifier_result: Optional[Literal["pass", "fail"]]
    retry_count: int
    escalation_reason: Optional[str]
    audit_trail: list[dict]


def new_state(
    request_id: str,
    raw_input: str,
    llm_budget: int = 5,
    department_fields: Optional[dict] = None,
    sub_department: Optional[str] = None,
    department_sequence: Optional[list[str]] = None,
    department_history: Optional[list[dict]] = None,
    department_fields_by_dept: Optional[dict] = None,
) -> GraphState:
    return {
        "request_id": request_id,
        "raw_input": raw_input,
        "request_type": None,
        "departments_activated": [],
        "llm_budget_remaining": llm_budget,
        "department_sequence": department_sequence,
        "department_history": department_history,
        "department_fields_by_dept": department_fields_by_dept,
        "active_department": None,
        "department_fields": department_fields,
        "sub_department": sub_department,
        "department_output": None,
        "verifier_result": None,
        "retry_count": 0,
        "escalation_reason": None,
        "audit_trail": [],
    }


def log_audit(state: GraphState, node: str, decision: str, source: str) -> None:
    state["audit_trail"].append(
        {
            "node": node,
            "decision": decision,
            "source": source,
            "request_id": state["request_id"],
        }
    )
