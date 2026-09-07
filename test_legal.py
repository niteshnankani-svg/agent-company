"""Live integration test for the locally running Legal RAG backend.

Requires the Legal RAG backend to be running at http://localhost:8000/query.
Connection refused means the local backend is not running; it is not itself a
code failure in this agent-company wrapper.
"""

try:
    import legal_compliance as legal_module
    from legal_compliance import legal_head_node
    from state import new_state
    from verifier import verifier_node
except ImportError:
    from . import legal_compliance as legal_module
    from .legal_compliance import legal_head_node
    from .state import new_state
    from .verifier import verifier_node

from unittest.mock import AsyncMock

def run_case(request_id: str, department_fields: dict) -> dict:
    state = new_state(
        request_id,
        department_fields["question"],
        department_fields=department_fields,
    )
    result = legal_head_node(state)
    print(f"\n{request_id} department_output:")
    print(result["department_output"])
    print(f"{request_id} audit_trail:")
    print(result["audit_trail"])
    return result


def run_fallback_unit_test() -> None:
    fake_result = {
        "answer": "I cannot answer this question.",
        "citations": [
            {
                "act": "BNS",
                "section_number": "357",
                "title": "Breach of contract",
                "score": 0.6123,
            }
        ],
    }
    original = legal_module.ask_legal_question
    legal_module.ask_legal_question = AsyncMock(return_value=fake_result)
    try:
        result = legal_head_node(
            new_state(
                "legal-fallback-unit",
                "unstructured question",
                department_fields={"question": "unstructured question"},
            )
        )
    finally:
        legal_module.ask_legal_question = original

    output = result["department_output"]
    assert output["decision"] == "BNS 357: Breach of contract"
    assert "no explicit section citation found" in output["rationale"]
    print("Fallback unit test: PASS")
    print("Fallback department_output:", output)


if __name__ == "__main__":
    run_fallback_unit_test()

    murder_case = run_case(
        "legal-murder",
        {
            "question": "What is the punishment for murder under BNS?",
            "act_filter": "BNS",
        },
    )
    murder_output = murder_case["department_output"]
    murder_score = murder_output["confidence"] if murder_output["source"] == "llm" else None
    print(f"Scenario (a) actual top_score: {murder_score}")
    if murder_score is not None:
        assert murder_output["decision"] == "BNS Section 103(1)"
        assert murder_output["evidence_ids"]
        assert 0.0 <= murder_score <= 1.0
        assert murder_score == 0.6332
        assert "no explicit section citation found" not in murder_output["rationale"]
        print("Scenario (a) Legal head preserved the raw citation score.")

        verified_murder = verifier_node(murder_case)
        print(f"Scenario (a) verifier_result: {verified_murder['verifier_result']}")
        print(f"Scenario (a) verifier audit: {verified_murder['audit_trail'][-1]}")
        assert verified_murder["verifier_result"] in {"pass", "fail"}
    elif murder_output["decision"] == "human_escalation":
        print(
            "Scenario (a) blocked: the local Legal RAG backend was not "
            "reachable or returned no usable legal grounding."
        )
    else:
        confidence = murder_output["confidence"]
        print(f"Scenario (a) actual confidence score: {confidence}")
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0
        assert murder_output["evidence_ids"]
        assert len(murder_output["evidence_ids"]) > 1
        assert "[BNS, Section" in murder_output["rationale"]

    gibberish_case = run_case(
        "legal-gibberish",
        # This is a nonsensical question that retrieves citations but at low
        # confidence; it is not a length-validation test.
        {"question": "asdkjfh"},
    )
    gibberish_output = gibberish_case["department_output"]
    gibberish_score = (
        gibberish_output["confidence"]
        if gibberish_output["source"] == "llm"
        else None
    )
    print(f"Scenario (b) actual top_score: {gibberish_score}")
    if gibberish_score is not None:
        assert 0.0 <= gibberish_score <= 1.0
        assert gibberish_output["decision"] != "human_escalation"
        print("Scenario (b) low score is delegated to the shared verifier.")
    else:
        print("Scenario (b) score unavailable because the backend was unreachable.")

    short_case = run_case(
        "legal-too-short-question",
        {"question": "hi"},
    )
    short_output = short_case["department_output"]
    print("Scenario (c) actual top_score: N/A (backend validation error expected)")
    assert short_output["decision"] == "human_escalation"
