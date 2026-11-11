"""Graph behaviour: partial updates (no in-place mutation), routing, and the API shape."""
import copy

import pytest

import graph as graph_module
import legal_compliance
import orchestrator
from contract import DepartmentResult
from graph import (
    HEAD_NODES, MAX_RETRIES, build_graph, human_escalation_node, make_head_node,
    orchestrator_entry_node, route_after_orchestrator, route_after_sequence_step,
    route_after_verifier, sequence_step, unsupported_department_node,
)
from state import audit_entry, new_state
from verifier import verifier_node

AR = dict(age=34, income=65000.0, loan_amount=25000.0, credit_score=610, years_employed=4, missed_payments=2)
CIT = {"act": "BNS", "section_number": "357", "title": "T", "summary": "S", "score": 0.64}


@pytest.fixture(autouse=True)
def fresh_routing_cache():
    orchestrator.orchestrator_cache._store.clear()
    yield
    orchestrator.orchestrator_cache._store.clear()


def returns(value):
    async def fake(*a, **k):
        return value
    return fake


def assert_pure(node, state):
    """A node must return a dict update and leave the state it was given untouched."""
    before = copy.deepcopy(state)
    update = node(state)
    assert isinstance(update, dict)
    assert state == before, "node mutated its input state"
    assert set(update) <= set(state), f"update has keys not in GraphState: {set(update) - set(state)}"
    return update


# ---------------------------------------------------------------- partial updates
def test_head_node_returns_only_what_it_changes():
    state = new_state("r1", "credit check", department_fields=AR, sub_department="ar")
    update = assert_pure(make_head_node("finance"), state)
    assert set(update) == {"department_result", "active_department", "audit_trail"}
    assert update["active_department"] == "finance"
    assert DepartmentResult.from_state(update["department_result"]).status == "needs_human"
    assert update["audit_trail"] == [
        audit_entry(state, "finance_head", "needs_human", department="finance", reason="credit_model_not_validated")
    ]


def test_orchestrator_cache_path_is_pure(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    state = new_state("r2", "What is Section 357 of BNS about?")
    update = assert_pure(orchestrator.orchestrator_node, state)  # fills the routing cache
    assert update["request_type"] == "legal_compliance"
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: pytest.fail("cache should answer"))
    again = assert_pure(orchestrator.orchestrator_node, state)
    assert again["audit_trail"][0]["source"] == "cache"


def test_orchestrator_llm_fallback_returns_the_new_budget_instead_of_mutating(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.1))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: ("finance", 0.85, budget - 1))
    state = new_state("r3", "something odd", llm_budget=5)
    update = assert_pure(orchestrator.orchestrator_node, state)
    assert update["llm_budget_remaining"] == 4 and state["llm_budget_remaining"] == 5


def test_verifier_is_pure_and_checks_structure():
    ok = new_state("r4", "x")
    ok["department_result"] = DepartmentResult(department="d", status="needs_human", reason="x").to_state()
    update = assert_pure(verifier_node, ok)
    assert update["verifier_result"] == "pass" and "retry_count" not in update

    for bad in (None, {"department": "d", "status": "answered"}):
        s = new_state("r5", "x")
        s["department_result"] = bad
        update = assert_pure(verifier_node, s)
        assert update["verifier_result"] == "fail" and update["retry_count"] == 1


def test_verifier_passes_every_valid_status_it_does_not_judge_truth_yet():
    for kw in (dict(status="failed", error="down"), dict(status="needs_more_info", missing_fields=["a"])):
        s = new_state("r", "x")
        s["department_result"] = DepartmentResult(department="d", **kw).to_state()
        assert verifier_node(s)["verifier_result"] == "pass"


def test_sequence_step_does_not_pop_the_callers_list():
    state = new_state(
        "r6", "text", department_sequence=["finance", "no_such_dept"],
        department_fields_by_dept={"finance": {"fields": AR, "sub_department": "ar"}},
    )
    update = assert_pure(sequence_step, state)
    assert update["department_sequence"] == ["no_such_dept"] and state["department_sequence"] == ["finance", "no_such_dept"]
    assert update["department_history"][0]["department"] == "finance"


def test_unsupported_and_human_nodes_are_pure():
    update = assert_pure(unsupported_department_node, new_state("r7", "x"))
    assert DepartmentResult.from_state(update["department_result"]).status == "needs_human"
    assert "escalation_reason" in assert_pure(human_escalation_node, new_state("r8", "x"))


def test_an_unexpected_exception_in_a_department_becomes_failed_not_a_crash(monkeypatch):
    import departments

    def boom(raw, fields, sub): raise ValueError("secret detail that must not leak")
    monkeypatch.setitem(departments.RUNNERS, "finance", boom)
    update = make_head_node("finance")(new_state("r9", "x"))
    result = DepartmentResult.from_state(update["department_result"])
    assert result.status == "failed" and result.error == "unexpected_error: ValueError"
    assert "secret" not in result.error


# ------------------------------------------------------------------------ routing
def test_routing_covers_every_department_and_falls_back_for_unknown():
    for dept, node in HEAD_NODES.items():
        assert route_after_orchestrator({"request_type": dept}) == node
    for other in ("human_escalation", None, "astrology"):
        assert route_after_orchestrator({"request_type": other}) == "unsupported_department"
    assert route_after_orchestrator({"request_type": "finance", "department_sequence": ["x"]}) == "sequence_step"


def test_sequence_routing():
    assert route_after_sequence_step({"department_sequence": ["a"]}) == "continue"
    assert route_after_sequence_step({"department_sequence": []}) == "done"


def test_verifier_routing_pass_retry_cap_and_unknown():
    for n in (0, 1, MAX_RETRIES + 2):
        assert route_after_verifier({"verifier_result": "pass", "retry_count": n}) == "done"
    for dept, node in HEAD_NODES.items():
        for n in range(MAX_RETRIES):
            assert route_after_verifier({"verifier_result": "fail", "retry_count": n, "active_department": dept}) == node
        assert route_after_verifier({"verifier_result": "fail", "retry_count": MAX_RETRIES, "active_department": dept}) == "human_escalation"
    assert route_after_verifier({"verifier_result": "fail", "retry_count": 0, "active_department": None}) == "human_escalation"


def test_entry_node_skips_classification_for_an_explicit_sequence(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: pytest.fail("must not classify"))
    update = assert_pure(orchestrator_entry_node, new_state("r10", "x", department_sequence=["finance"]))
    assert update["audit_trail"][0]["status"] == "sequential_request"


# ------------------------------------------------------------- compiled graph, e2e
def run(state):
    return build_graph().invoke(state)


def test_end_to_end_legal_answered(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", returns({"answer": "Section 357 ...", "citations": [CIT]}))
    final = run(new_state("e1", "What is Section 357 of BNS about?"))
    result = DepartmentResult.from_state(final["department_result"])
    assert result.status == "answered" and result.confidence.kind == "retrieval_score"
    assert [e["node"] for e in final["audit_trail"]] == ["orchestrator", "legal_head", "verifier"]
    assert final["verifier_result"] == "pass" and final["retry_count"] == 0


def test_end_to_end_finance_without_fields_asks_for_them(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("finance", 0.9))
    final = run(new_state("e2", "please process the vendor invoice"))
    result = DepartmentResult.from_state(final["department_result"])
    assert result.status == "needs_more_info" and result.missing_fields == ["sub_department"]


def test_end_to_end_service_down_is_a_failed_result_and_is_not_retried(monkeypatch):
    calls = []
    async def down(*a, **k):
        calls.append(1)
        return {"error": "request_failed", "detail": "All connection attempts failed"}
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", down)
    final = run(new_state("e3", "law question here"))
    assert DepartmentResult.from_state(final["department_result"]).status == "failed"
    assert len(calls) == 1, "a failed department must not be blindly re-run"


def test_end_to_end_unclassifiable_request_needs_a_human(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.0))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: ("human_escalation", 0.0, budget))
    final = run(new_state("e4", "asdkjfh aslkdjf help"))
    result = DepartmentResult.from_state(final["department_result"])
    assert result.status == "needs_human" and result.reason == "request_not_classified"
    assert [e["node"] for e in final["audit_trail"]] == ["orchestrator", "unsupported_department"]


def test_end_to_end_sequence_includes_legal_and_keeps_history(monkeypatch):
    monkeypatch.setattr(legal_compliance, "ask_legal_question", returns({"answer": "A", "citations": [CIT]}))
    final = run(new_state(
        "e5", "text", department_sequence=["legal_compliance", "finance"],
        department_fields_by_dept={"finance": {"fields": AR, "sub_department": "ar"}},
    ))
    assert [h["department"] for h in final["department_history"]] == ["legal_compliance", "finance"]
    assert final["department_history"][0]["result"]["status"] == "answered"
    assert DepartmentResult.from_state(final["department_result"]).department == "finance"


# --------------------------------------------------------------------------- API
def test_api_response_is_the_contract(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import api

    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", returns({"answer": "Section 357 ...", "citations": [CIT]}))
    body = TestClient(api.app).post("/request", json={"request_id": "a1", "raw_input": "What is Section 357 of BNS about?"}).json()
    assert set(body) == {"request_id", "result", "needs_human", "escalation_reason", "department_history", "audit_trail"}
    assert DepartmentResult.model_validate(body["result"]).status == "answered"
    assert body["needs_human"] is False


def test_api_reports_needs_human_for_unclassified_requests(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import api

    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.0))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: ("human_escalation", 0.0, budget))
    body = TestClient(api.app).post("/request", json={"request_id": "a2", "raw_input": "???"}).json()
    assert body["needs_human"] is True  # previously reported false for this case
