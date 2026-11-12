"""The DepartmentResult contract: the invariants a department cannot get wrong."""
import math

import pytest
from pydantic import ValidationError

from contract import Confidence, DepartmentResult, Evidence, service_error, truncate

EV = Evidence(source="svc", text="some text")


def make(**kw):
    base = dict(department="d", status="answered", answer="a", evidence=[EV])
    base.update(kw)
    return DepartmentResult(**base)


def test_valid_answered():
    r = make()
    assert r.status == "answered"
    assert r.confidence.kind == "none" and r.confidence.value is None


@pytest.mark.parametrize("kw", [
    dict(answer=None), dict(answer="  "), dict(evidence=[]), dict(error="boom"),
])
def test_answered_requires_answer_and_evidence_and_no_error(kw):
    with pytest.raises(ValidationError):
        make(**kw)


def test_needs_human_requires_reason():
    with pytest.raises(ValidationError):
        DepartmentResult(department="d", status="needs_human")
    assert DepartmentResult(department="d", status="needs_human", reason="x").reason == "x"


def test_needs_more_info_requires_missing_fields():
    with pytest.raises(ValidationError):
        DepartmentResult(department="d", status="needs_more_info")
    r = DepartmentResult(department="d", status="needs_more_info", missing_fields=["age"])
    assert r.missing_fields == ["age"]


def test_missing_fields_only_with_needs_more_info():
    with pytest.raises(ValidationError):
        make(missing_fields=["age"])


def test_failed_requires_error():
    with pytest.raises(ValidationError):
        DepartmentResult(department="d", status="failed")
    with pytest.raises(ValidationError):
        DepartmentResult(department="d", status="failed", error="  ")
    assert DepartmentResult(department="d", status="failed", error="down").error == "down"


def test_unknown_status_rejected():
    with pytest.raises(ValidationError):
        DepartmentResult(department="d", status="maybe")


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        make(decision="old-style")


@pytest.mark.parametrize("kw", [dict(text=None, data=None), dict(text="  "), dict(data={})])
def test_evidence_must_carry_content_not_just_a_label(kw):
    with pytest.raises(ValidationError):
        Evidence(source="svc", **kw)


def test_evidence_with_data_only_is_fine():
    assert Evidence(source="svc", data={"k": 1}).data == {"k": 1}


def test_confidence_none_must_not_have_value():
    with pytest.raises(ValidationError):
        Confidence(kind="none", value=0.9)


@pytest.mark.parametrize("kind", ["calibrated_probability", "retrieval_score"])
def test_confidence_with_kind_needs_a_finite_value(kind):
    for bad in (None, math.nan, math.inf):
        with pytest.raises(ValidationError):
            Confidence(kind=kind, value=bad)


def test_calibrated_probability_is_bounded_but_retrieval_score_is_not():
    with pytest.raises(ValidationError):
        Confidence(kind="calibrated_probability", value=1.2)
    assert Confidence(kind="retrieval_score", value=1.2).value == 1.2


def test_unknown_confidence_kind_rejected():
    with pytest.raises(ValidationError):
        Confidence(kind="vibes", value=0.5)


def test_state_round_trip_is_json_safe_and_lossless():
    import json

    r = make(confidence=Confidence(kind="retrieval_score", value=0.64))
    d = r.to_state()
    json.dumps(d)  # must be plain JSON data so it can be checkpointed
    assert DepartmentResult.from_state(d) == r


def test_helpers_bound_remote_text():
    assert len(truncate("x" * 1000)) == 300
    msg = service_error("svc", {"error": "http_status", "detail": "y" * 1000})
    assert msg.startswith("svc: http_status (") and len(msg) < 400
    assert service_error("svc", {"error": "timeout"}) == "svc: timeout"
