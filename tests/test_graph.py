"""Graph behaviour: partial updates (no in-place mutation), routing, and the API shape."""
import copy

import pytest

import graph as graph_module
import legal_compliance
import orchestrator
from contract import DepartmentResult
from graph import (
    HEAD_NODES, build_graph, make_head_node, make_queue_review_node,
    orchestrator_entry_node, route_after_orchestrator, route_after_sequence_step,
    route_after_verifier, sequence_step,
)
from helpers import supports_everything, invoke_graph
from state import audit_entry, new_state
from verifier import verifier_node

AR = dict(age=34, income=65000.0, loan_amount=25000.0, credit_score=610, years_employed=4, missed_payments=2)
CIT = {"act": "BNS", "section_number": "357", "title": "T", "summary": "S", "score": 0.64}


def returns(value):
    async def fake(*a, **k):
        return value
    return fake


def assert_pure(node, state, *args):
    """A node must return a dict update and leave the state it was given untouched."""
    before = copy.deepcopy(state)
    update = node(state, *args)
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


def test_orchestrator_routes_confident_jev_answer_and_is_pure(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda *a: pytest.fail("LLM must not be asked"))
    state = new_state("r2", "What is Section 357 of BNS about?")
    update = assert_pure(orchestrator.orchestrator_node, state)
    assert update["request_type"] == "legal_compliance"
    assert update["audit_trail"][0]["source"] == "local_model"


def test_orchestrator_llm_fallback_returns_the_new_budget_instead_of_mutating(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.1))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: ("finance", "ok", budget - 1))
    state = new_state("r3", "something odd", llm_budget=5)
    update = assert_pure(orchestrator.orchestrator_node, state)
    assert update["request_type"] == "finance"
    assert update["llm_budget_remaining"] == 4 and state["llm_budget_remaining"] == 5


def test_verifier_is_pure_and_fails_a_missing_or_malformed_result():
    ok = new_state("r4", "x")
    ok["department_result"] = DepartmentResult(department="d", status="needs_human", reason="x").to_state()
    update = assert_pure(verifier_node, ok)
    assert update["verifier_result"] == "pass" and "retry_count" not in update

    for bad in (None, {"department": "d", "status": "answered"}):
        s = new_state("r5", "x")
        s["department_result"] = bad
        update = assert_pure(verifier_node, s)
        assert update["verifier_result"] == "fail" and update["escalation_reason"] == "invalid_result"


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


class RecordingInbox:
    def __init__(self): self.added = []
    def add(self, *args): self.added.append(args)


CONFIG = {"configurable": {"thread_id": "run-123"}}


def test_queue_review_builds_the_held_result_and_files_it_in_the_inbox():
    inbox = RecordingInbox()
    state = new_state("r7", "something odd")
    state["escalation_reason"] = "request_not_classified"
    update = assert_pure(make_queue_review_node(inbox), state, CONFIG)
    result = DepartmentResult.from_state(update["department_result"])
    assert result.status == "needs_human" and result.reason == "request_not_classified"
    assert update["audit_trail"][0]["node"] == "queue_review" and update["audit_trail"][0]["status"] == "awaiting_review"
    assert inbox.added == [("run-123", "r7", "orchestrator", "request_not_classified", "something odd", False)]


def test_queue_review_keeps_a_departments_own_held_result_and_notes_whether_there_is_a_draft():
    inbox = RecordingInbox()
    state = new_state("r7b", "x")
    held = DepartmentResult(department="legal_compliance", status="needs_human", reason="verification_failed", answer="draft").to_state()
    state.update(department_result=held, escalation_reason="verification_failed", active_department="legal_compliance")
    update = assert_pure(make_queue_review_node(inbox), state, CONFIG)
    assert "department_result" not in update  # left exactly as the department/verifier left it
    assert inbox.added[0][2:] == ("legal_compliance", "verification_failed", "x", True)


def test_queue_review_takes_the_reason_from_the_departments_result_when_none_was_set():
    state = new_state("r7c", "x")
    state["department_result"] = DepartmentResult(department="backend_ticketing", status="needs_human", reason="security_incident_review").to_state()
    assert make_queue_review_node(None)(state, CONFIG)["escalation_reason"] == "security_incident_review"


def test_an_unexpected_exception_in_a_department_becomes_failed_not_a_crash(monkeypatch):
    import departments

    def boom(raw, fields, sub, follow_up, feedback): raise ValueError("secret detail that must not leak")
    monkeypatch.setitem(departments.RUNNERS, "finance", boom)
    update = make_head_node("finance")(new_state("r9", "x"))
    result = DepartmentResult.from_state(update["department_result"])
    assert result.status == "failed" and result.error == "unexpected_error: ValueError"
    assert "secret" not in result.error


# ------------------------------------------------------------------------ routing
def test_routing_sends_every_department_through_the_cache_and_everything_else_to_a_human():
    for dept in HEAD_NODES:
        assert route_after_orchestrator({"request_type": dept}) == "cache_lookup"
    for other in ("human_escalation", None, "astrology"):
        assert route_after_orchestrator({"request_type": other}) == "queue_review"
    assert route_after_orchestrator({"request_type": "finance", "department_sequence": ["x"]}) == "sequence_step"


def test_sequence_routing():
    assert route_after_sequence_step({"department_sequence": ["a"]}) == "continue"
    assert route_after_sequence_step({"department_sequence": []}) == "done"


def test_verifier_routing_pass_retry_and_fail():
    assert route_after_verifier({"verifier_result": "pass", "department_result": {"status": "answered"}}) == "cache_store"
    # a result a department itself marked needs_human always goes to a person
    assert route_after_verifier({"verifier_result": "pass", "department_result": {"status": "needs_human"}}) == "queue_review"
    for dept, node in HEAD_NODES.items():
        assert route_after_verifier({"verifier_result": "retry", "active_department": dept}) == node
    assert route_after_verifier({"verifier_result": "retry", "active_department": None}) == "queue_review"
    assert route_after_verifier({"verifier_result": "fail"}) == "queue_review"


def test_entry_node_skips_classification_for_an_explicit_sequence(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: pytest.fail("must not classify"))
    update = assert_pure(orchestrator_entry_node, new_state("r10", "x", department_sequence=["finance"]))
    assert update["audit_trail"][0]["status"] == "sequential_request"


# ------------------------------------------------------------- compiled graph, e2e
def run(state):
    return invoke_graph(state)


def test_end_to_end_legal_answered(monkeypatch):
    supports_everything(monkeypatch)
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", returns({"answer": "Section 357 ...", "citations": [CIT]}))
    final = run(new_state("e1", "What is Section 357 of BNS about?"))
    result = DepartmentResult.from_state(final["department_result"])
    assert result.status == "answered" and result.confidence.kind == "retrieval_score"
    assert [e["node"] for e in final["audit_trail"]] == [
        "orchestrator", "cache_lookup", "legal_head", "verifier", "cache_store"]
    assert final["verifier_result"] == "pass" and final["retry_count"] == 0
    assert final["verification"]["status"] == "verified"


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


def test_end_to_end_unsure_classifiers_go_to_the_review_queue(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.0))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "unsure", budget - 1))
    final = run(new_state("e4", "asdkjfh aslkdjf help"))
    result = DepartmentResult.from_state(final["department_result"])
    assert result.status == "needs_human" and result.reason == "request_not_classified"
    assert [e["node"] for e in final["audit_trail"]] == ["orchestrator", "queue_review"]
    assert final["escalation_reason"] == "request_not_classified"


def test_end_to_end_classifier_outage_is_reported_as_such_not_a_crash(monkeypatch):
    def down(text): raise ConnectionError("TypeSafe unreachable")
    monkeypatch.setattr(orchestrator, "local_classify", down)
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "error", budget - 1))
    final = run(new_state("e4b", "anything"))
    result = DepartmentResult.from_state(final["department_result"])
    assert result.status == "needs_human" and result.reason == "classifier_unavailable"


def test_end_to_end_sequence_includes_legal_and_keeps_history(monkeypatch):
    supports_everything(monkeypatch)
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
    supports_everything(monkeypatch)
    from fastapi.testclient import TestClient

    import api

    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", returns({"answer": "Section 357 ...", "citations": [CIT]}))
    body = TestClient(api.app).post("/request", json={"request_id": "a1", "raw_input": "What is Section 357 of BNS about?"}).json()
    assert set(body) == {"run_id", "request_id", "result", "needs_human", "pending_review", "verification", "escalation_reason", "department_history", "audit_trail"}
    assert DepartmentResult.model_validate(body["result"]).status == "answered"
    assert body["needs_human"] is False


def test_api_reports_needs_human_for_unclassified_requests(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import api

    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.0))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "unsure", budget))
    body = TestClient(api.app).post("/request", json={"request_id": "a2", "raw_input": "???"}).json()
    assert body["needs_human"] is True  # previously reported false for this case
