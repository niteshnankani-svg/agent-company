"""The LangGraph wiring. Every node returns a partial state update; none mutate state."""
from langgraph.graph import END, StateGraph

from cache import get_cache, why_not_cacheable
from contract import DepartmentResult
from departments import run_department
from orchestrator import orchestrator_node
from state import GraphState, audit_entry, new_state, result_update
from verifier import verifier_node

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
            bool(state.get("follow_up")),
            state.get("feedback"),
        )
        return result_update(state, node_name, result)

    head_node.__name__ = node_name
    return head_node


def orchestrator_entry_node(state: GraphState) -> dict:
    """Skip classification for a sequence, or for a follow-up form the system generated itself."""
    if state.get("department_sequence"):
        return {
            "audit_trail": [audit_entry(state, "orchestrator", "sequential_request")]
        }
    pinned = state.get("pinned_department")
    if pinned in HEAD_NODES:
        return {
            "request_type": pinned,
            "audit_trail": [
                audit_entry(state, "orchestrator", "routed", department=pinned, source="follow_up")
            ],
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
    if state["request_type"] in HEAD_NODES:
        return "cache_lookup"
    # "human_escalation" (the router could not decide or was unreachable) and any
    # unexpected value both go to a person, explicitly, by name.
    return "human_review"


def cache_key(department: str, state: GraphState) -> tuple[str, str]:
    """(text, scope) the cache is keyed on: the text the department actually answers."""
    fields = state.get("department_fields") or {}
    if department == "legal_compliance":
        return fields.get("question") or state["raw_input"], str(fields.get("act_filter") or "")
    return state["raw_input"], ""


def cache_lookup_node(state: GraphState) -> dict:
    """Before running a department: reuse a stored, verified answer if policy allows."""
    department = state["request_type"]
    text, scope = cache_key(department, state)
    skip = why_not_cacheable(department, text)
    if skip:
        return {"audit_trail": [audit_entry(state, "cache_lookup", "skipped", reason=skip)]}
    cache, note = get_cache()
    if cache is None:
        return {"audit_trail": [audit_entry(state, "cache_lookup", "disabled", reason=note)]}
    hit = cache.lookup(department, text, scope)
    if hit is None:
        return {"audit_trail": [audit_entry(state, "cache_lookup", "miss")]}
    result = DepartmentResult.from_state(hit.result)
    update = result_update(state, "cache_lookup", result)
    update["audit_trail"] = [
        audit_entry(
            state, "cache_lookup", "hit", department=department,
            similarity=round(hit.similarity, 4), age_seconds=round(hit.age_seconds),
        )
    ]
    update["cache_hit"] = True
    # Only verified answers are ever stored, so a hit was verified when it was stored.
    update["verification"] = {"status": "verified", "check": "cached"}
    return update


def route_after_cache(state: GraphState) -> str:
    return "done" if state.get("cache_hit") else HEAD_NODES[state["request_type"]]


def cache_store_node(state: GraphState) -> dict:
    """After the verifier passed a result: remember it if policy allows. Never before verification."""
    department = state.get("active_department") or ""
    if state.get("department_history"):
        return {"audit_trail": [audit_entry(state, "cache_store", "skipped", reason="sequential run")]}
    raw = state.get("department_result")
    if not raw:
        return {"audit_trail": [audit_entry(state, "cache_store", "skipped", reason="no result")]}
    result = DepartmentResult.from_state(raw)
    text, scope = cache_key(department, state)
    reason = why_not_cacheable(department, text, result)
    if reason is None and (state.get("verification") or {}).get("status") != "verified":
        reason = "only verified answers are cached"
    if reason is None:
        cache, note = get_cache()
        if cache is None:
            reason = note
        else:
            reason = cache.store(department, text, result, scope)
    status = "stored" if reason is None else "not_cached"
    return {"audit_trail": [audit_entry(state, "cache_store", status, reason=reason)]}


def route_after_verifier(state: GraphState) -> str:
    """pass -> remember it; retry -> the same department, now with feedback; fail -> a person.

    On "fail" the verifier has already withdrawn the answer (it is ``needs_human`` by now).
    """
    outcome = state["verifier_result"]
    if outcome == "pass":
        return "cache_store"
    if outcome == "retry":
        # Unknown/unset active_department fails closed to human review.
        return HEAD_NODES.get(state.get("active_department"), "human_review")
    return "human_review"


def human_review_node(state: GraphState) -> dict:
    """Where every request a machine cannot finish ends up.

    Reached when the router could not pick a department, or the verifier withdrew an answer
    (the result it left behind is already ``needs_human``, with the draft and its findings).
    It always yields a ``needs_human`` result with a reason. Today it records the hand-off and
    ends the run; Phase 5 makes it pause the run with ``interrupt()`` until a person decides.
    """
    reason = state.get("escalation_reason") or "unspecified"
    update: dict = {
        "escalation_reason": reason,
        "audit_trail": [audit_entry(state, "human_review", "needs_human", reason=reason)],
    }
    if not state.get("department_result"):
        result = DepartmentResult(
            department=state.get("active_department") or "orchestrator",
            status="needs_human",
            reason=reason,
        )
        update["department_result"] = result.to_state()
        update["active_department"] = result.department
    return update


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("orchestrator", orchestrator_entry_node)
    graph.add_node("sequence_step", sequence_step)
    graph.add_node("cache_lookup", cache_lookup_node)
    for department, node_name in HEAD_NODES.items():
        graph.add_node(node_name, make_head_node(department))
    graph.add_node("verifier", verifier_node)
    graph.add_node("cache_store", cache_store_node)
    graph.add_node("human_review", human_review_node)

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "sequence_step": "sequence_step",
            "cache_lookup": "cache_lookup",
            "human_review": "human_review",
        },
    )
    graph.add_conditional_edges(
        "cache_lookup",
        route_after_cache,
        {"done": END, **{node: node for node in HEAD_NODES.values()}},
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
            "cache_store": "cache_store",
            **{node: node for node in HEAD_NODES.values()},
            "human_review": "human_review",
        },
    )
    graph.add_edge("cache_store", END)
    graph.add_edge("human_review", END)
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
