from langgraph.graph import END, StateGraph

try:
    from state import GraphState, log_audit, new_state
    from orchestrator import orchestrator_node
    from finance import finance_cache, finance_head_node, llm_escalate_finance
    from verifier import register_department_cache, verifier_node
except ModuleNotFoundError as exc:
    if exc.name not in {"state", "orchestrator", "finance", "verifier", "cache"}:
        raise
    from .state import GraphState, log_audit, new_state
    from .orchestrator import orchestrator_node
    from .finance import finance_cache, finance_head_node, llm_escalate_finance
    from .verifier import register_department_cache, verifier_node


MAX_RETRIES = 3

register_department_cache("finance", finance_cache)


def finance_node_wrapper(state: GraphState) -> GraphState:
    if state["retry_count"] == 0:
        return finance_head_node(
            state, state["department_fields"], state["sub_department"]
        )
    prior_output = state["department_output"]
    new_output = llm_escalate_finance(prior_output, state)
    state["department_output"] = new_output
    log_audit(
        state,
        "finance_head_retry_escalation",
        new_output["decision"],
        source=new_output["source"],
    )
    return state


def orchestrator_entry_node(state: GraphState) -> GraphState:
    """Keep the existing entry point while bypassing classification for a sequence.

    ``orchestrator.py`` is intentionally left unchanged because it is a
    protected department-independent component. This graph-level guard gives
    explicit sequential requests the same behavior: no cache lookup, local
    classification, or LLM call can overwrite their caller-supplied sequence.
    """
    if state.get("department_sequence"):
        log_audit(state, "orchestrator", "sequential_request", source="local_model")
        return state
    return orchestrator_node(state)


def sequence_step(state: GraphState) -> GraphState:
    """Run the next explicitly requested department and preserve its output."""
    if state.get("department_history") is None:
        state["department_history"] = []

    dept = state["department_sequence"].pop(0)
    dept_config = (state.get("department_fields_by_dept") or {}).get(dept, {})
    state["department_fields"] = dept_config.get("fields", {})

    if dept == "finance":
        try:
            if __package__:
                from .finance import finance_head_node
            else:
                from finance import finance_head_node
        except ModuleNotFoundError as exc:
            raise ImportError(
                f"finance department unavailable in sequence: {exc}"
            ) from exc
        state = finance_head_node(
            state,
            dept_config.get("fields", {}),
            dept_config.get("sub_department"),
        )
    elif dept == "hr_complaints":
        try:
            if __package__:
                from .hr_complaints import hr_head_node
            else:
                from hr_complaints import hr_head_node
        except ModuleNotFoundError as exc:
            raise ImportError(f"hr_complaints unavailable in sequence: {exc}") from exc
        state = hr_head_node(state)
    elif dept == "backend_ticketing":
        try:
            if __package__:
                from .backend_ticketing import backend_head_node
            else:
                from backend_ticketing import backend_head_node
        except ModuleNotFoundError as exc:
            raise ImportError(
                f"backend_ticketing unavailable in sequence: {exc}"
            ) from exc
        state = backend_head_node(state)
    elif dept == "export_compliance":
        try:
            if __package__:
                from .export_compliance import export_head_node
            else:
                from export_compliance import export_head_node
        except ModuleNotFoundError as exc:
            raise ImportError(
                f"export_compliance unavailable in sequence: {exc}"
            ) from exc
        state = export_head_node(state)
    else:
        state["department_output"] = {
            "decision": "unsupported_department",
            "confidence": 0.0,
            "rationale": f"No department head exists for '{dept}' in sequence",
            "evidence_ids": [],
            "source": "local_model",
        }

    state["department_history"].append(
        {"department": dept, "output": state["department_output"]}
    )
    return state


def route_after_sequence_step(state: GraphState) -> str:
    """Continue until the final department, then verify only its output.

    Earlier outputs remain in department_history for audit purposes but are
    not separately re-verified. Verifying every intermediate step is a
    deliberate future extension, not part of this build because no real
    scenario has required it yet.
    """
    if state.get("department_sequence"):
        return "continue"
    return "done"


def route_after_orchestrator(state: GraphState) -> str:
    if state.get("department_sequence"):
        return "sequence_step"
    if state["request_type"] == "finance":
        return "finance_head"
    if state["request_type"] == "hr_complaints":
        return "hr_head"
    if state["request_type"] == "backend_ticketing":
        return "backend_head"
    if state["request_type"] == "export_compliance":
        return "export_head"
    if state["request_type"] == "legal_compliance":
        return "legal_head"
    return "unsupported_department"


# A failed verification must retry the department that produced the output —
# not always finance. Every department head sets state["active_department"].
_RETRY_TARGETS = {
    "finance": "finance_head",
    "hr_complaints": "hr_head",
    "backend_ticketing": "backend_head",
    "export_compliance": "export_head",
    "legal_compliance": "legal_head",
}


def route_after_verifier(state: GraphState) -> str:
    if state["verifier_result"] == "pass":
        return "done"
    # THIS is where the retry cap is actually enforced — verifier_node only
    # sets verifier_result and increments retry_count; it does not decide what
    # happens next. That decision belongs here, in the graph, not inside any
    # individual node.
    if state["retry_count"] >= MAX_RETRIES:
        return "human_escalation"
    # Unknown/unset active_department fails closed to human review rather than
    # silently retrying the wrong department.
    return _RETRY_TARGETS.get(state.get("active_department"), "human_escalation")


def unsupported_department_node(state: GraphState) -> GraphState:
    state["department_output"] = {
        "decision": "unsupported_department",
        "confidence": 0.0,
        "rationale": f"No department head exists yet for '{state['request_type']}'",
        "evidence_ids": [],
        "source": "local_model",
    }
    return state


def human_escalation_node(state: GraphState) -> GraphState:
    """Does NOT overwrite department_output — the last real decision and its
    evidence_ids are what a human reviewer needs, not a generic message that
    discards them.
    """
    state["escalation_reason"] = (
        f"Verifier failed {MAX_RETRIES} times — routed to human review. "
        "Last attempted decision shown was NOT verified."
    )
    return state


def build_graph():
    try:
        if __package__:
            from .hr_complaints import hr_cache, hr_head_node
        else:
            from hr_complaints import hr_cache, hr_head_node
    except ModuleNotFoundError as exc:
        raise ImportError(
            "HR department unavailable — hr_complaints.py could not import "
            "its dependencies (likely missing complaint_agent.py or "
            f"crewai/torch/transformers): {exc}"
        ) from exc

    try:
        if __package__:
            from .backend_ticketing import backend_head_node
        else:
            from backend_ticketing import backend_head_node
    except ModuleNotFoundError as exc:
        raise ImportError(
            "Backend/Ticketing unavailable — backend_ticketing.py could not "
            "import its dependencies (likely missing the mcp package): "
            f"{exc}"
        ) from exc

    try:
        if __package__:
            from .export_compliance import export_head_node
        else:
            from export_compliance import export_head_node
    except ModuleNotFoundError as exc:
        raise ImportError(
            "Export/Compliance unavailable — export_compliance.py could "
            "not import its dependencies (likely missing mcp/httpx): "
            f"{exc}"
        ) from exc

    try:
        if __package__:
            from .legal_compliance import legal_head_node
        else:
            from legal_compliance import legal_head_node
    except ModuleNotFoundError as exc:
        raise ImportError(
            "Legal unavailable — legal_compliance.py could not import "
            f"dependencies: {exc}"
        ) from exc

    register_department_cache("hr_complaints", hr_cache)
    graph = StateGraph(GraphState)
    graph.add_node("orchestrator", orchestrator_entry_node)
    graph.add_node("sequence_step", sequence_step)
    graph.add_node("finance_head", finance_node_wrapper)
    graph.add_node("hr_head", hr_head_node)
    graph.add_node("backend_head", backend_head_node)
    graph.add_node("export_head", export_head_node)
    graph.add_node("legal_head", legal_head_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("unsupported_department", unsupported_department_node)
    graph.add_node("human_escalation", human_escalation_node)
    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "sequence_step": "sequence_step",
            "finance_head": "finance_head",
            "hr_head": "hr_head",
            "backend_head": "backend_head",
            "export_head": "export_head",
            "legal_head": "legal_head",
            "unsupported_department": "unsupported_department",
        },
    )
    graph.add_conditional_edges(
        "sequence_step",
        route_after_sequence_step,
        {"continue": "sequence_step", "done": "verifier"},
    )
    graph.add_edge("finance_head", "verifier")
    graph.add_edge("hr_head", "verifier")
    graph.add_edge("backend_head", "verifier")
    graph.add_edge("export_head", "verifier")
    graph.add_edge("legal_head", "verifier")
    graph.add_conditional_edges(
        "verifier",
        route_after_verifier,
        {
            "done": END,
            "finance_head": "finance_head",
            "hr_head": "hr_head",
            "backend_head": "backend_head",
            "export_head": "export_head",
            "legal_head": "legal_head",
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
