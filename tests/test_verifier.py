"""The verifier: claims checked against evidence, one retry with feedback, and no unverified 'answered'."""
import pytest

import export_compliance
import hr_complaints
import legal_compliance
import orchestrator
import verifier
from contract import DepartmentResult, Evidence, with_feedback
from graph import build_graph
from state import new_state
from verifier import Finding, check_claims, evidence_text, feedback_from, verifier_node

EV = [Evidence(source="legal_rag:BNS Section 357", text="Breach of contract to attend on and supply wants of helpless person.", data={"score": 0.64})]


def answered(dept="legal_compliance", answer="Section 357 punishes breach of a contract to look after a helpless person.", evidence=None):
    return DepartmentResult(department=dept, status="answered", answer=answer, evidence=evidence or EV)


def state_with(result, **kw):
    s = new_state("v1", "What is Section 357 of BNS about?", **kw)
    s["department_result"] = result.to_state()
    s["active_department"] = result.department
    return s


def sequence(monkeypatch, *responses):
    """Install a checker that returns the given replies in order and records what it was asked."""
    calls, queue = [], list(responses)

    def fake(question, answer, evidence):
        calls.append({"question": question, "answer": answer, "evidence": evidence})
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(verifier, "_call_checker", fake)
    return calls


def good(quote="Breach of contract to attend on"):
    return {"claims": [{"claim": "357 is about breach of contract to look after a helpless person", "verdict": "supported", "quote": quote}]}


def bad(verdict="not_in_evidence", claim="the fine is five thousand rupees", **extra):
    return {"claims": [{"claim": claim, "verdict": verdict, **extra}]}


def no_checker(monkeypatch):
    monkeypatch.setattr(verifier, "_call_checker", lambda *a: pytest.fail("the checker must not be called here"))


# ------------------------------------------------------------- the checker is held to account
def test_a_supported_verdict_needs_a_quote_that_really_is_in_the_evidence(monkeypatch):
    sequence(monkeypatch, good("Breach of contract to attend on"))
    assert [f.verdict for f in check_claims("q", "a", EV)] == ["supported"]
    sequence(monkeypatch, good("a quote the evidence does not contain"))
    f = check_claims("q", "a", EV)[0]
    assert f.verdict == "not_in_evidence" and "not in the evidence" in f.explanation


def test_a_supported_verdict_with_no_quote_is_downgraded(monkeypatch):
    sequence(monkeypatch, {"claims": [{"claim": "x", "verdict": "supported"}]})
    assert check_claims("q", "a", EV)[0].verdict == "not_in_evidence"


def test_unknown_verdicts_and_malformed_items(monkeypatch):
    sequence(monkeypatch, {"claims": [{"claim": "x", "verdict": "probably"}, "junk", {"verdict": "supported"}, {"claim": " ", "verdict": "supported"}]})
    findings = check_claims("q", "a", EV)
    assert [(f.claim, f.verdict) for f in findings] == [("x", "not_in_evidence")]


@pytest.mark.parametrize("reply", [{}, {"claims": []}, {"claims": "nope"}, {"claims": ["junk"]}])
def test_a_checker_that_returns_no_usable_claims_is_an_error_not_a_pass(monkeypatch, reply):
    sequence(monkeypatch, reply)
    with pytest.raises(ValueError):
        check_claims("q", "a", EV)


def test_evidence_text_includes_source_text_and_data_and_is_bounded():
    text = evidence_text(EV)
    assert "[legal_rag:BNS Section 357]" in text and "helpless person" in text and '"score": 0.64' in text
    huge = [Evidence(source=f"s{i}", text="x" * 5000) for i in range(10)]
    assert len(evidence_text(huge)) <= verifier.MAX_EVIDENCE_CHARS


class FakeClient:
    def __init__(self, blocks): self.blocks, self.kwargs, self.messages = blocks, None, self
    def create(self, **kw):
        self.kwargs = kw
        return type("R", (), {"content": self.blocks})()


def test_the_model_is_asked_through_a_forced_tool_with_everything_fenced_as_data(monkeypatch):
    block = type("B", (), {"type": "tool_use", "input": good()})()
    client = FakeClient([block])
    monkeypatch.undo()
    monkeypatch.setattr(verifier, "get_bedrock_client", lambda: client)
    out = verifier._call_checker("What is 357?", "It is about X.", "[src]\nevidence text")
    assert out == good()
    kw = client.kwargs
    assert kw["tool_choice"] == {"type": "tool", "name": "record_claim_verdicts"} and kw["temperature"] == 0
    content = kw["messages"][0]["content"]
    assert all(tag in content for tag in ("<question>", "<answer>", "<evidence>")) and "evidence text" in content
    assert "never use outside knowledge" in kw["system"] and "not instructions" in kw["system"]


def test_feedback_names_the_specific_unsupported_claims_only():
    text = feedback_from([
        Finding("357 is about contracts", "supported", "q"),
        Finding("the fine is 5000 rupees", "not_in_evidence"),
        Finding("it applies to minors", "contradicted", "q", "the evidence says adults"),
    ])
    assert "the fine is 5000 rupees" in text and "not found in the evidence" in text
    assert "it applies to minors" in text and "contradicted by the evidence" in text and "the evidence says adults" in text
    assert "357 is about contracts" not in text


# ---------------------------------------------------------------- what is never sent to a model
@pytest.mark.parametrize("kw", [
    dict(status="needs_human", reason="x"), dict(status="failed", error="e"),
    dict(status="needs_more_info", missing_fields=["a"]),
])
def test_results_that_are_not_answered_are_not_checked(monkeypatch, kw):
    no_checker(monkeypatch)
    update = verifier_node(state_with(DepartmentResult(department="legal_compliance", **kw)))
    assert update["verifier_result"] == "pass" and update["verification"]["check"] == "skipped_status"
    assert update["verification"]["status"] == "not_applicable"


def test_a_needs_human_result_with_a_draft_answer_is_still_not_checked(monkeypatch):
    no_checker(monkeypatch)
    r = DepartmentResult(department="legal_compliance", status="needs_human", reason="x", answer="a draft", evidence=EV)
    assert verifier_node(state_with(r))["verification"]["check"] == "skipped_status"


def test_rule_based_finance_is_not_sent_to_a_model(monkeypatch):
    no_checker(monkeypatch)
    update = verifier_node(state_with(answered("finance")))
    assert update["verifier_result"] == "pass"
    assert update["verification"] == {"status": "unchecked", "check": "rule_based"}


def test_backend_answers_pass_as_unchecked_because_there_are_no_sources(monkeypatch):
    no_checker(monkeypatch)
    update = verifier_node(state_with(answered("backend_ticketing", evidence=[Evidence(source="ticketing_agent", data={"category": "hw"})])))
    assert update["verifier_result"] == "pass"
    assert update["verification"] == {"status": "unchecked", "check": "no_source_material"}  # never "verified"


# ------------------------------------------------------------------ claims: pass, retry, fail
def test_all_claims_supported_is_verified_and_the_checker_sees_the_answer_and_evidence(monkeypatch):
    calls = sequence(monkeypatch, good())
    s = state_with(answered(), llm_budget=5)
    s["department_fields"] = {"question": "What does section 357 say?"}
    update = verifier_node(s)
    assert update["verifier_result"] == "pass" and update["verification"]["status"] == "verified"
    assert update["llm_budget_remaining"] == 4
    c = calls[0]
    assert c["question"] == "What does section 357 say?"
    assert "helpless person" in c["answer"] and "legal_rag:BNS Section 357" in c["evidence"]


def test_an_unsupported_claim_asks_for_exactly_one_retry_with_specific_feedback(monkeypatch):
    sequence(monkeypatch, bad())
    update = verifier_node(state_with(answered()))
    assert update["verifier_result"] == "retry" and update["retry_count"] == 1
    assert "the fine is five thousand rupees" in update["feedback"]
    assert "department_result" not in update  # the answer stays as it is until the retry replaces it
    assert update["verification"]["status"] == "retrying"


def test_after_the_one_retry_a_still_unsupported_answer_is_withdrawn_not_released(monkeypatch):
    sequence(monkeypatch, bad())
    s = state_with(answered())
    s["retry_count"] = 1
    update = verifier_node(s)
    assert update["verifier_result"] == "fail" and update["escalation_reason"] == "verification_failed"
    held = DepartmentResult.from_state(update["department_result"])
    assert held.status == "needs_human" and held.reason == "verification_failed"
    assert held.answer.startswith("Section 357")  # kept as a draft for the reviewer
    assert held.evidence[-1].source == "verifier" and "the fine is five thousand rupees" in held.evidence[-1].text
    assert update["verification"]["status"] == "failed"


@pytest.mark.parametrize("dept", ["hr_complaints", "export_compliance", "legal_compliance"])
def test_departments_that_can_use_feedback_get_a_retry(monkeypatch, dept):
    sequence(monkeypatch, bad())
    assert verifier_node(state_with(answered(dept)))["verifier_result"] == "retry"


def test_an_unknown_department_is_checked_and_never_retried(monkeypatch):
    sequence(monkeypatch, bad())
    assert verifier_node(state_with(answered("mystery")))["verifier_result"] == "fail"
    sequence(monkeypatch, good())
    assert verifier_node(state_with(answered("mystery")))["verification"]["status"] == "verified"


def test_a_contradicted_claim_fails_the_answer_too(monkeypatch):
    sequence(monkeypatch, bad("contradicted", quote="Breach of contract", explanation="evidence says otherwise"))
    assert verifier_node(state_with(answered()))["verifier_result"] == "retry"


def test_one_unsupported_claim_among_supported_ones_still_fails(monkeypatch):
    sequence(monkeypatch, {"claims": [good()["claims"][0], bad()["claims"][0]]})
    assert verifier_node(state_with(answered()))["verifier_result"] == "retry"


# --------------------------------------------------------------------- checker unavailable
@pytest.mark.parametrize("exc", [RuntimeError("no credentials"), TimeoutError("slow"), ValueError("no claims")])
def test_if_the_checker_cannot_run_the_answer_is_withdrawn_by_default(monkeypatch, exc):
    sequence(monkeypatch, exc)
    update = verifier_node(state_with(answered()))
    held = DepartmentResult.from_state(update["department_result"])
    assert update["verifier_result"] == "fail" and held.status == "needs_human" and held.reason == "verification_unavailable"
    assert "verification" in update and update["verification"]["check"] == "checker_unavailable"


def test_degraded_mode_can_release_answers_but_labels_them_unchecked(monkeypatch):
    monkeypatch.setenv("VERIFIER_ON_UNAVAILABLE", "release_unchecked")
    sequence(monkeypatch, RuntimeError("down"))
    update = verifier_node(state_with(answered()))
    assert update["verifier_result"] == "pass" and update["verification"]["status"] == "unchecked"
    assert "department_result" not in update


def test_no_llm_budget_means_unavailable_and_no_call(monkeypatch):
    no_checker(monkeypatch)
    update = verifier_node(state_with(answered(), llm_budget=0))
    assert DepartmentResult.from_state(update["department_result"]).reason == "verification_unavailable"


# ----------------------------------------------------- departments use the feedback
def returns(value):
    async def fake(*a, **k): return value
    return fake


def capture(monkeypatch, module, name, value):
    seen = []
    async def fake(question, *rest):
        seen.append(question)
        return value
    monkeypatch.setattr(module, name, fake)
    return seen


def test_with_feedback_marks_the_note_and_bounds_it():
    assert with_feedback("q", None) == "q"
    out = with_feedback("q", "x" * 5000)
    assert out.startswith("q\n\n[Reviewer feedback on your previous attempt: ") and len(out) < 1000


def test_legal_sends_the_feedback_to_its_service(monkeypatch):
    seen = capture(monkeypatch, legal_compliance, "ask_legal_question", {"answer": "A", "citations": [{"act": "BNS", "section_number": "1", "title": "T", "score": 0.5}]})
    legal_compliance.run_legal("What is 357?", {}, "FEEDBACK-TEXT")
    legal_compliance.run_legal("What is 357?", {})
    assert "FEEDBACK-TEXT" in seen[0] and seen[1] == "What is 357?"


def test_export_sends_the_feedback_to_the_chat_service(monkeypatch):
    seen = capture(monkeypatch, export_compliance, "ask_export_question", {"error": "timeout", "detail": "x"})
    export_compliance.run_export("cotton t-shirts", {}, "FEEDBACK-TEXT")
    assert "What is the HS code for cotton t-shirts?" in seen[0] and "FEEDBACK-TEXT" in seen[0]


def test_hr_gives_the_feedback_to_the_agent_but_not_to_the_complaint_gate(monkeypatch):
    gate = []
    monkeypatch.setattr(hr_complaints, "complaint_probability", lambda t: gate.append(t) or 0.01)
    seen = capture(monkeypatch, hr_complaints, "ask_hr_agent", {"answer": "42", "evidence": "rows"})
    hr_complaints.run_hr("How many in Sales?", {}, "FEEDBACK-TEXT")
    assert gate == ["How many in Sales?"] and "FEEDBACK-TEXT" in seen[0]


def test_export_answer_states_the_product_so_the_match_can_be_checked(monkeypatch):
    monkeypatch.setattr(export_compliance, "ask_export_question", returns({"full_answer": "HS Code: 61091000", "hs_code": "61091000", "extraction_ok": True}))
    monkeypatch.setattr(export_compliance, "get_trade_data", returns({"found": True, "data": [{"Commodity": "T-SHIRTS, COTTON"}], "intel": {}}))
    r = export_compliance.run_export("cotton t-shirts", {})
    assert r.answer.startswith("HS code 61091000 applies to cotton t-shirts; TradeStat lists it as: T-SHIRTS, COTTON")


def test_every_department_is_in_the_policy_table():
    import departments
    assert set(verifier.POLICY) == set(departments.DEPARTMENT_NAMES)


# ---------------------------------------------------------------------------- the graph
CIT = {"act": "BNS", "section_number": "357", "title": "Breach of contract", "summary": "S", "score": 0.64}


def run_legal_graph(monkeypatch, checker_replies, answers):
    """Legal answers come from `answers` in order; the checker replies come from `checker_replies`."""
    asked = []
    queue = list(answers)

    async def fake(question, act_filter):
        asked.append(question)
        return {"answer": queue.pop(0) if len(queue) > 1 else queue[0], "citations": [CIT]}

    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", fake)
    calls = sequence(monkeypatch, *checker_replies)
    final = build_graph().invoke(new_state("g1", "What is Section 357 of BNS about?"))
    return final, asked, calls


def trail(final): return [(e["node"], e["status"]) for e in final["audit_trail"]]


def test_graph_unsupported_then_supported_retries_once_with_the_feedback(monkeypatch):
    final, asked, calls = run_legal_graph(monkeypatch, [bad(), good("Breach of contract: S")], ["Answer with a made-up fine.", "Answer without it."])
    r = DepartmentResult.from_state(final["department_result"])
    assert r.status == "answered" and r.answer == "Answer without it."
    assert len(asked) == 2 and "made-up" not in asked[0] and "the fine is five thousand rupees" in asked[1]
    assert final["retry_count"] == 1 and final["verification"]["status"] == "verified"
    assert trail(final) == [
        ("orchestrator", "routed"), ("cache_lookup", "disabled"), ("legal_head", "answered"),
        ("verifier", "retry"), ("legal_head", "answered"), ("verifier", "pass"), ("cache_store", "not_cached"),
    ]
    assert len(calls) == 2 and final["llm_budget_remaining"] == 3  # each check spends one unit


def test_graph_caps_at_one_retry_then_hands_a_withdrawn_draft_to_a_person(monkeypatch):
    final, asked, calls = run_legal_graph(monkeypatch, [bad()], ["Always unsupported."])
    r = DepartmentResult.from_state(final["department_result"])
    assert len(asked) == 2, "the service is called exactly twice: the attempt and ONE retry"
    assert r.status == "needs_human" and r.reason == "verification_failed" and r.answer == "Always unsupported."
    assert final["escalation_reason"] == "verification_failed" and final["verification"]["status"] == "failed"
    assert trail(final) == [
        ("orchestrator", "routed"), ("cache_lookup", "disabled"), ("legal_head", "answered"), ("verifier", "retry"),
        ("legal_head", "answered"), ("verifier", "fail"), ("human_review", "needs_human"),
    ]


def test_graph_a_failed_department_is_never_retried(monkeypatch):
    calls = []
    async def down(*a, **k):
        calls.append(1)
        return {"error": "timeout", "detail": "60s"}
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", down)
    no_checker(monkeypatch)
    final = build_graph().invoke(new_state("g2", "law question"))
    assert len(calls) == 1 and DepartmentResult.from_state(final["department_result"]).status == "failed"


def test_graph_checker_down_withdraws_the_answer(monkeypatch):
    final, asked, _ = run_legal_graph(monkeypatch, [RuntimeError("down")], ["An answer."])
    r = DepartmentResult.from_state(final["department_result"])
    assert r.status == "needs_human" and r.reason == "verification_unavailable" and len(asked) == 1


def test_graph_only_verified_answers_are_cached(monkeypatch):
    import cache as cache_module
    from test_cache import make  # reuse the word-hash embedder
    cache_module.set_cache(make())
    monkeypatch.setenv("VERIFIER_ON_UNAVAILABLE", "release_unchecked")
    final, _, _ = run_legal_graph(monkeypatch, [RuntimeError("down")], ["An answer."])
    assert DepartmentResult.from_state(final["department_result"]).status == "answered"
    assert final["verification"]["status"] == "unchecked"
    assert ("cache_store", "not_cached") in trail(final) and len(cache_module.get_cache()[0]) == 0
    assert [e["reason"] for e in final["audit_trail"] if e["node"] == "cache_store"] == ["only verified answers are cached"]


def test_graph_export_retry_re_asks_the_chat_service_with_the_feedback(monkeypatch):
    questions, queue = [], [
        {"full_answer": "HS 62034200", "hs_code": "62034200", "extraction_ok": True},
        {"full_answer": "HS 61091000", "hs_code": "61091000", "extraction_ok": True},
    ]
    async def chat(q):
        questions.append(q)
        return queue.pop(0)
    async def trade(code):
        return {"found": True, "data": [{"Commodity": "TROUSERS" if code == "62034200" else "T-SHIRTS, COTTON"}], "intel": {}}
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("export_compliance", 0.9))
    monkeypatch.setattr(export_compliance, "ask_export_question", chat)
    monkeypatch.setattr(export_compliance, "get_trade_data", trade)
    sequence(monkeypatch, bad("contradicted", claim="HS code 62034200 applies to cotton t-shirts", quote="TROUSERS"), good("T-SHIRTS, COTTON"))
    final = build_graph().invoke(new_state("g3", "cotton t-shirts"))
    r = DepartmentResult.from_state(final["department_result"])
    assert r.status == "answered" and "61091000" in r.answer
    assert len(questions) == 2 and "HS code 62034200 applies to cotton t-shirts" in questions[1]


def test_graph_backend_answers_are_labelled_unchecked_end_to_end(monkeypatch):
    import backend_ticketing
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("backend_ticketing", 0.9))
    monkeypatch.setattr(backend_ticketing, "ask_ticketing_agent", returns({"status": "answered", "category": "hw", "answer": "Hold the power button."}))
    no_checker(monkeypatch)
    final = build_graph().invoke(new_state("g4", "laptop will not boot"))
    assert DepartmentResult.from_state(final["department_result"]).status == "answered"
    assert final["verification"] == {"status": "unchecked", "check": "no_source_material"}
