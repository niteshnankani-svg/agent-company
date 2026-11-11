"""The LangGraph wiring. Every node returns a partial state update; none mutate state."""
from langgraph.graph import END, StateGraph

from contract import DepartmentResult
from departments import run_department
from orchestrator import orchestrator_node
from state import GraphState, audit_entry, new_state, result_update
from verifier import verifier_node

MAX_RETRIES = 3

# department name -> graph node name
HEAD_NODES = {
    "finance": "finance_head",
    "hr_complaints": "hr_head",
    "backend_ticketing": "backend_head",
    "export_compliance": "export_head",
    "legal_compliance": "legal_head",
}


def make_head_node(department: str):
    node_name = HEAD_NODES[department]

    def head_node(state: GraphState) -> dict:
        result = run_department(
            department,
            state["raw_input"],
            state.get("department_fields"),
            state.get("sub_department"),
        )
        return result_update(state, node_name, result)

    head_node.__name__ = node_name
    return head_node


def orchestrator_entry_node(state: GraphState) -> dict:
    """Skip classification when the caller supplied an explicit department sequence."""
    if state.get("department_sequence"):
        return {
            "audit_trail": [audit_entry(state, "orchestrator", "sequential_request")]
        }
    return orchestrator_node(state)


def sequence_step(state: GraphState) -> dict:
    """Run the next department of an explicitly requested sequence."""
    remaining = list(state["department_sequence"])
    department = remaining.pop(0)
    config = (state.get("department_fields_by_dept") or {}).get(department, {})
    fields = config.get("fields", {})
    result = run_department(
        department, state["raw_input"], fields, config.get("sub_department")
    )
    update = result_update(state, "sequence_step", result)
    update.update(
        {
            "department_sequence": remaining,
            "department_fields": fields,
            "department_history": [
                {"department": department, "result": result.to_state()}
            ],
        }
    )
    return update


def route_after_sequence_step(state: GraphState) -> str:
    """Continue until the last department; only its result is verified."""
    return "continue" if state.get("department_sequence") else "done"


def route_after_orchestrator(state: GraphState) -> str:
    if state.get("department_sequence"):
        return "sequence_step"
    return HEAD_NODES.get(state["request_type"], "unsupported_department")


# A failed verification retries the department that produced the result.
# (Replaced in Phase 4: retry only with specific feedback, at most once.)
def route_after_verifier(state: GraphState) -> str:
    if state["verifier_result"] == "pass":
        return "done"
    if state["retry_count"] >= MAX_RETRIES:
        return "human_escalation"
    # Unknown/unset active_department fails closed to human review.
    return HEAD_NODES.get(state.get("active_department"), "human_escalation")


def unsupported_department_node(state: GraphState) -> dict:
    """The orchestrator could not pick a department. Replaced by a real human node in Phase 2."""
    result = DepartmentResult(
        department="orchestrator",
        status="needs_human",
        reason="request_not_classified",
    )
    return result_update(state, "unsupported_department", result)


def human_escalation_node(state: GraphState) -> dict:
    """Reached only when the verifier keeps failing. Replaced by a pausing node in Phase 5."""
    return {
        "escalation_reason": (
            f"Verifier failed {MAX_RETRIES} times; routed to human review. "
            "Whatever result is shown was NOT verified."
        ),
        "audit_trail": [audit_entry(state, "human_escalation", "needs_human")],
    }


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("orchestrator", orchestrator_entry_node)
    graph.add_node("sequence_step", sequence_step)
    for department, node_name in HEAD_NODES.items():
        graph.add_node(node_name, make_head_node(department))
    graph.add_node("verifier", verifier_node)
    graph.add_node("unsupported_department", unsupported_department_node)
    graph.add_node("human_escalation", human_escalation_node)

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "sequence_step": "sequence_step",
            **{node: node for node in HEAD_NODES.values()},
            "unsupported_department": "unsupported_department",
        },
    )
    graph.add_conditional_edges(
        "sequence_step",
        route_after_sequence_step,
        {"continue": "sequence_step", "done": "verifier"},
    )
    for node_name in HEAD_NODES.values():
        graph.add_edge(node_name, "verifier")
    graph.add_conditional_edges(
        "verifier",
        route_after_verifier,
        {
            "done": END,
            **{node: node for node in HEAD_NODES.values()},
            "human_escalation": "human_escalation",
        },
    )
    graph.add_edge("unsupported_department", END)
    graph.add_edge("human_escalation", END)
    return graph.compile()


def build_initial_state(
    request_id: str,
    raw_input: str,
    department_fields: dict,
    sub_department: str,
) -> GraphState:
    return new_state(
        request_id,
        raw_input,
        department_fields=department_fields,
        sub_department=sub_department,
    )
