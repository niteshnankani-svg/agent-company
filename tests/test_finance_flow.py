"""Finance from a plain sentence plus a follow-up form: AR and AP, end to end."""
import pytest
from pydantic import ValidationError

import extraction
import orchestrator
from contract import DepartmentResult, FieldSpec, FollowUp
from finance import run_finance
from graph import build_graph
from state import new_state


def model_says(monkeypatch, payload):
    calls = []
    def fake(text, defs):
        calls.append([d.name for d in defs])
        return payload
    monkeypatch.setattr(extraction, "_call_model", fake)
    return calls


AP_SENTENCE = "Please pay invoice INV-4021 from Bhilwara Textiles for ₹45,000"
AP_MODEL = {
    "sub_department": {"value": "ap", "quote": "pay invoice INV-4021"},
    "invoice_amount": {"value": 45000, "quote": "₹45,000"},
}
AR_SENTENCE = "Credit check: applicant aged 34 earning ₹65,000, wants a loan of 25000, credit score 610, employed 4 years, 2 missed payments"
AR_MODEL = {
    "sub_department": {"value": "ar", "quote": "Credit check"},
    "age": {"value": 34, "quote": "aged 34"},
    "income": {"value": 65000, "quote": "₹65,000"},
    "loan_amount": {"value": 25000, "quote": "loan of 25000"},
    "credit_score": {"value": 610, "quote": "credit score 610"},
    "years_employed": {"value": 4, "quote": "employed 4 years"},
    "missed_payments": {"value": 2, "quote": "2 missed payments"},
}
FORM_ANSWERS = {
    "vendor_is_first_time": False, "vendor_average_invoice": "30000", "is_duplicate_invoice_number": False,
    "payment_terms_days": "30", "days_outstanding": "40",
}


def names(r): return [f.name for f in r.follow_up.fields]


# ------------------------------------------------------------- AP: sentence + form
def test_ap_round_one_a_plain_sentence_yields_a_form_for_what_is_missing(monkeypatch):
    model_says(monkeypatch, AP_MODEL)
    r = run_finance(AP_SENTENCE, {}, None)
    assert r.status == "needs_more_info" and r.reason == "missing_fields"
    assert r.missing_fields == names(r) == [
        "vendor_is_first_time", "vendor_average_invoice", "is_duplicate_invoice_number",
        "payment_terms_days", "days_outstanding",
    ]
    assert r.follow_up.sub_department == "ap" and r.follow_up.collected == {"invoice_amount": 45000.0}
    kinds = {f.name: f.kind for f in r.follow_up.fields}
    assert kinds["vendor_is_first_time"] == "boolean" and kinds["payment_terms_days"] == "integer"
    # the user is never asked for what code derives or the policy does not use
    assert not {"is_round_number", "overdue", "gst_rate_pct", "interstate", "amount_vs_vendor_avg_ratio"} & set(r.missing_fields)


def test_ap_round_two_the_form_answers_reach_the_ap_policy(monkeypatch):
    calls = model_says(monkeypatch, AP_MODEL)
    r1 = run_finance(AP_SENTENCE, {}, None)
    calls.clear()
    fields = {**r1.follow_up.collected, **FORM_ANSWERS}
    r2 = run_finance(AP_SENTENCE, fields, r1.follow_up.sub_department, follow_up=True)
    assert calls == [], "the model must not be asked again after the form"
    assert r2.status == "needs_human" and r2.reason == "payment_requires_human_approval"
    assert "recommendation: approve" in r2.answer and "no payment executed" in r2.answer
    data = r2.evidence[0].data
    assert data["input"]["amount_vs_vendor_avg_ratio"] == 1.5 and data["input"]["overdue"] is True
    assert data["input"]["is_round_number"] is False
    prov = data["provenance"]
    assert prov["invoice_amount"].startswith("entered by the user")  # it came back through the form
    assert prov["amount_vs_vendor_avg_ratio"].startswith("derived") and prov["overdue"].startswith("derived")


def test_provenance_records_which_values_were_stated_in_the_sentence(monkeypatch):
    model_says(monkeypatch, AP_MODEL)
    r = run_finance(AP_SENTENCE, FORM_ANSWERS, "ap")  # one round: form answers sent up front, rest from the sentence
    assert r.status == "needs_human"
    assert r.evidence[0].data["provenance"]["invoice_amount"] == "stated in the request: “₹45,000”"
    assert r.evidence[0].data["provenance"]["vendor_is_first_time"] == "entered by the user"


@pytest.mark.parametrize("change,expected", [
    ({"days_outstanding": "75"}, "hold"),                    # 75 days on 30-day terms: more than 30 past
    ({"is_duplicate_invoice_number": True}, "flag"),
    ({"vendor_average_invoice": "10000"}, "flag"),           # 45000 / 10000 = 4.5, above 2.5
    ({}, "approve"),
])
def test_ap_policy_results_follow_from_the_form(change, expected):
    fields = {"invoice_amount": 45000, **FORM_ANSWERS, **change}
    r = run_finance(AP_SENTENCE, fields, "ap", follow_up=True)
    assert r.status == "needs_human", r.model_dump()
    assert f"recommendation: {expected}" in r.answer


def test_first_time_vendor_needs_no_average_and_the_ratio_rule_is_skipped(monkeypatch):
    model_says(monkeypatch, {"invoice_amount": {"value": 60000, "quote": "₹60,000"}})
    answers = {"vendor_is_first_time": True, "is_duplicate_invoice_number": False, "payment_terms_days": 30, "days_outstanding": 5}
    r = run_finance("Pay a new vendor ₹60,000", answers, "ap")
    assert r.status == "needs_human"
    assert "large_first_time_vendor_invoice" in r.answer and "amount_far_above_vendor_average" not in r.answer


def test_average_is_asked_only_when_first_time_is_not_known_to_be_true(monkeypatch):
    model_says(monkeypatch, {})
    base = {"invoice_amount": 1000, "is_duplicate_invoice_number": False, "payment_terms_days": 30, "days_outstanding": 5}
    unknown = run_finance("x", base, "ap")
    assert "vendor_average_invoice" in unknown.missing_fields and "vendor_is_first_time" in unknown.missing_fields
    first = run_finance("x", {**base, "vendor_is_first_time": True}, "ap", follow_up=True)
    assert first.status == "needs_human"


# ---------------------------------------------------------------------------- AR
def test_ar_everything_stated_in_the_sentence_goes_straight_to_the_ar_path(monkeypatch):
    model_says(monkeypatch, AR_MODEL)
    r = run_finance(AR_SENTENCE, {}, None)
    assert r.status == "needs_human" and r.reason == "credit_model_not_validated"
    assert r.evidence[0].data["input"] == {
        "age": 34, "income": 65000.0, "loan_amount": 25000.0, "credit_score": 610, "years_employed": 4, "missed_payments": 2}


def test_ar_partial_sentence_asks_only_for_the_rest_and_then_completes(monkeypatch):
    partial = {k: v for k, v in AR_MODEL.items() if k in ("sub_department", "age", "income")}
    model_says(monkeypatch, partial)
    r1 = run_finance(AR_SENTENCE, {}, None)
    assert r1.status == "needs_more_info" and r1.follow_up.sub_department == "ar"
    assert r1.missing_fields == ["loan_amount", "credit_score", "years_employed", "missed_payments"]
    assert r1.follow_up.collected == {"age": 34, "income": 65000.0}
    answers = {**r1.follow_up.collected, "loan_amount": "25000", "credit_score": "610", "years_employed": "4", "missed_payments": "2"}
    r2 = run_finance(AR_SENTENCE, answers, "ar", follow_up=True)
    assert r2.status == "needs_human" and r2.reason == "credit_model_not_validated"


# ----------------------------------------------------------------- path handling
def test_unknown_path_asks_for_the_path_first_then_for_its_fields(monkeypatch):
    model_says(monkeypatch, {})
    r1 = run_finance("I need help with a finance thing", {}, None)
    assert r1.missing_fields == ["sub_department"] and r1.reason == "finance_path_not_specified"
    assert set(r1.follow_up.fields[0].choices) == {"ar", "ap"}
    r2 = run_finance("I need help with a finance thing", {}, "ar", follow_up=True)
    assert r2.follow_up.sub_department == "ar" and "credit_score" in r2.missing_fields


def test_after_the_user_picks_the_path_the_numbers_in_the_sentence_are_still_used(monkeypatch):
    """The form for 'which path?' sends the choice in fields; the sentence's numbers must not be lost."""
    calls = model_says(monkeypatch, {"invoice_amount": {"value": 45000, "quote": "₹45,000"}})
    r = run_finance(AP_SENTENCE, {"sub_department": "ap"}, None, follow_up=True)
    assert len(calls) == 1 and "age" not in calls[0] and "invoice_amount" in calls[0]
    assert r.follow_up.collected == {"invoice_amount": 45000.0} and r.follow_up.sub_department == "ap"


def test_a_path_in_the_fields_dict_is_honoured(monkeypatch):
    model_says(monkeypatch, {})
    assert run_finance("x", {"sub_department": "ar"}, None, follow_up=True).follow_up.sub_department == "ar"
    assert run_finance("x", {"sub_department": "nonsense"}, None, follow_up=True).missing_fields == ["sub_department"]


# --------------------------------------------------- the model failing or lying
def test_model_unavailable_degrades_to_a_form_for_everything_not_an_error(monkeypatch):
    r = run_finance(AP_SENTENCE, {}, "ap")  # conftest model raises
    assert r.status == "needs_more_info" and r.reason == "extraction_unavailable"
    assert "invoice_amount" in r.missing_fields and r.follow_up.collected == {}
    path = run_finance(AP_SENTENCE, {}, None)
    assert path.missing_fields == ["sub_department"] and path.reason == "extraction_unavailable"


def test_a_hallucinated_value_is_not_used_and_is_asked_for_instead(monkeypatch):
    model_says(monkeypatch, {"sub_department": {"value": "ap", "quote": "pay invoice"},
                             "invoice_amount": {"value": 450000, "quote": "₹45,000"}})  # 10x too big
    r = run_finance(AP_SENTENCE, {}, None)
    assert "invoice_amount" in r.missing_fields and "invoice_amount" not in r.follow_up.collected


def test_values_the_user_typed_beat_what_the_model_found(monkeypatch):
    model_says(monkeypatch, AP_MODEL)
    r = run_finance(AP_SENTENCE, {**FORM_ANSWERS, "invoice_amount": 50000}, "ap")
    assert r.evidence[0].data["input"]["invoice_amount"] == 50000.0
    assert r.evidence[0].data["provenance"]["invoice_amount"] == "entered by the user"


def test_the_model_is_only_asked_about_fields_still_unknown(monkeypatch):
    calls = model_says(monkeypatch, {})
    run_finance("x", {"invoice_amount": 100}, "ap")
    assert "invoice_amount" not in calls[0] and "payment_terms_days" in calls[0]
    assert "age" not in calls[0]  # the other path's fields are not asked about


# -------------------------------------------------------------- bad form input
def test_a_bad_form_value_is_asked_again_with_the_problem_shown(monkeypatch):
    model_says(monkeypatch, {})
    answers = {"age": 34, "income": 65000, "loan_amount": 25000, "credit_score": 900, "years_employed": 4, "missed_payments": 2}
    r = run_finance("x", answers, "ar", follow_up=True)
    assert r.status == "needs_more_info" and r.reason == "invalid_fields" and r.missing_fields == ["credit_score"]
    assert "850" in r.follow_up.fields[0].problem
    assert "credit_score" not in r.follow_up.collected and r.follow_up.collected["age"] == 34


def test_unparseable_form_value_is_reported_on_that_field(monkeypatch):
    r = run_finance("x", {**{"age": "old", "income": 1, "loan_amount": 1, "credit_score": 600, "years_employed": 1, "missed_payments": 0}}, "ar", follow_up=True)
    assert r.missing_fields == ["age"] and r.follow_up.fields[0].problem == "Please enter a number."


def test_an_absurd_average_is_blamed_on_the_field_the_user_can_change(monkeypatch):
    model_says(monkeypatch, {})
    r = run_finance("x", {**FORM_ANSWERS, "invoice_amount": 100000, "vendor_average_invoice": 1}, "ap", follow_up=True)
    assert r.status == "needs_more_info" and r.missing_fields == ["vendor_average_invoice"]


def test_old_style_complete_records_still_work_and_inconsistent_flags_are_named(monkeypatch):
    legacy = dict(invoice_amount=45000, amount_vs_vendor_avg_ratio=1.1, vendor_is_first_time=False, is_round_number=False,
                  is_duplicate_invoice_number=False, gst_rate_pct=5, interstate=True, payment_terms_days=30,
                  days_outstanding=52, overdue=True)
    assert run_finance("x", legacy, "ap", follow_up=True).status == "needs_human"
    r = run_finance("x", {**legacy, "overdue": False}, "ap", follow_up=True)
    assert r.status == "needs_more_info" and r.missing_fields == ["overdue"]


# ------------------------------------------------------------ contract: follow_up
def test_follow_up_must_match_missing_fields_and_only_goes_with_needs_more_info():
    spec = FieldSpec(name="a", label="A", kind="number")
    with pytest.raises(ValidationError):
        DepartmentResult(department="d", status="needs_more_info", missing_fields=["b"], follow_up=FollowUp(fields=[spec]))
    with pytest.raises(ValidationError):
        DepartmentResult(department="d", status="needs_human", reason="x", follow_up=FollowUp(fields=[spec]))
    with pytest.raises(ValidationError):
        FieldSpec(name="c", label="C", kind="choice")


# --------------------------------------------------------- graph and API, e2e
def test_graph_round_trip_sentence_then_form_without_routing_twice(monkeypatch):
    calls = model_says(monkeypatch, AP_MODEL)
    routed = []
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: routed.append(t) or ("finance", 0.9))
    first = build_graph().invoke(new_state("t1", AP_SENTENCE))
    r1 = DepartmentResult.from_state(first["department_result"])
    assert r1.status == "needs_more_info" and r1.follow_up.sub_department == "ap" and len(routed) == 1

    second = build_graph().invoke(new_state(
        "t2", AP_SENTENCE, department_fields={**r1.follow_up.collected, **FORM_ANSWERS},
        sub_department=r1.follow_up.sub_department, pinned_department=r1.department, follow_up=True))
    r2 = DepartmentResult.from_state(second["department_result"])
    assert r2.status == "needs_human" and r2.reason == "payment_requires_human_approval"
    assert len(routed) == 1, "the follow-up must not be classified again"
    assert second["audit_trail"][0]["source"] == "follow_up"
    assert len(calls) == 1, "and must not call the extraction model again"


def api_client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app)


def test_api_follow_up_request_reaches_finance(monkeypatch):
    model_says(monkeypatch, {})
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: pytest.fail("a follow-up is never routed"))
    body = {"request_id": "a1", "raw_input": AP_SENTENCE,
            "follow_up": {"department": "finance", "sub_department": "ap",
                          "fields": {"invoice_amount": 45000, **FORM_ANSWERS}}}
    out = api_client().post("/request", json=body).json()
    assert out["result"]["status"] == "needs_human" and out["result"]["department"] == "finance"
    assert out["needs_human"] is True


def test_api_follow_up_with_an_unknown_department_is_rejected():
    body = {"request_id": "a2", "raw_input": "x", "follow_up": {"department": "payroll", "fields": {}}}
    assert api_client().post("/request", json=body).status_code == 422


def test_api_needs_more_info_carries_the_form(monkeypatch):
    model_says(monkeypatch, AP_MODEL)
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("finance", 0.9))
    out = api_client().post("/request", json={"request_id": "a3", "raw_input": AP_SENTENCE}).json()
    res = out["result"]
    assert res["status"] == "needs_more_info" and out["needs_human"] is False
    assert [f["name"] for f in res["follow_up"]["fields"]] == res["missing_fields"]
