"""Live integration test for the local AI Complaint Intelligence Agent.

This test requires network access to Hugging Face and a live ANTHROPIC_API_KEY.
The negative-review case sends complaint text through the real CrewAI/Anthropic
pipeline; it must not be replaced with a mocked success.
"""

import os
from unittest.mock import patch

from hr_complaints import hr_cache, hr_head_node
from state import new_state


if not os.environ.get("ANTHROPIC_API_KEY"):
    raise SystemExit(
        "SKIPPED: test_hr.py requires ANTHROPIC_API_KEY and network access to "
        "Hugging Face/Anthropic for the real pipeline."
    )


positive_raw_input = "Amazing product, works perfectly!"
positive_fields = {
    "complaint_text": positive_raw_input,
    "category": "Appliances",
}

negative_raw_input = "Battery completely dead after 2 days."
negative_fields = {
    "complaint_text": negative_raw_input,
    "category": "Electronics",
}


def run_positive_case():
    state = new_state(
        "hr-positive",
        positive_raw_input,
        department_fields=positive_fields,
    )
    with patch(
        "hr_complaints.analyze_reviews",
        side_effect=AssertionError("CrewAI must not run for positive sentiment"),
    ) as crew_mock:
        result = hr_head_node(state)
    passed = (
        result["department_output"]["decision"] == "not_a_complaint"
        and result["department_output"]["source"] == "local_model"
        and crew_mock.call_count == 0
    )
    print("a) positive review:", "PASS" if passed else "FAIL")
    print("   output:", result["department_output"])
    print("   CrewAI calls:", crew_mock.call_count)
    assert passed
    return result


def run_negative_case():
    state = new_state(
        "hr-negative",
        negative_raw_input,
        department_fields=negative_fields,
    )
    result = hr_head_node(state)
    output = result["department_output"]
    passed = (
        output["decision"] == "requires_review"
        and output["source"] == "llm"
        and result["llm_budget_remaining"] == 1
        and "failed" not in output["rationale"].lower()
    )
    print("b) negative review / CrewAI report:", "PASS" if passed else "FAIL")
    print("   output:", output)
    print("   report:", output["rationale"])
    assert passed
    return result


def run_cache_case(positive_result):
    # This is a test-only seed. Production cache writes happen only in verifier.py.
    hr_cache.set(positive_raw_input, positive_result["department_output"])
    state = new_state(
        "hr-positive-cache",
        positive_raw_input,
        department_fields=positive_fields,
    )
    result = hr_head_node(state)
    passed = result["audit_trail"][-1]["source"] == "cache"
    print("c) repeated positive review / cache:", "PASS" if passed else "FAIL")
    print("   output:", result["department_output"])
    assert passed


if __name__ == "__main__":
    positive_result = run_positive_case()
    run_negative_case()
    run_cache_case(positive_result)
