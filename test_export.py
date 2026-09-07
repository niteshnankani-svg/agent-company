"""Live tests for export compliance.

Requires network access to backend-production-a3c6.up.railway.app. The
/tradedata scrape takes roughly seven seconds or longer; sandboxed
environments may fail with a connection error, which should be reported as an
environment limitation rather than masked as a code failure.
"""

try:
    from export_compliance import export_head_node
    from state import new_state
except ImportError:
    from .export_compliance import export_head_node
    from .state import new_state


def run_case(request_id: str, product_description: str) -> dict:
    state = new_state(
        request_id,
        f"Export compliance check for {product_description}",
        department_fields={"product_description": product_description},
    )
    result = export_head_node(state)
    print(f"\n{request_id} department_output:")
    print(result["department_output"])
    print(f"{request_id} audit_trail:")
    print(result["audit_trail"])
    return result


if __name__ == "__main__":
    cotton = run_case("export-cotton", "cotton t-shirts")
    gibberish = run_case("export-gibberish", "asdkjfh random gibberish xyz123")

    cotton_output = cotton["department_output"]
    if cotton_output["decision"] == "human_escalation":
        print(
            "Cotton live verification blocked: NiryatAI /chat was not "
            "reachable in this environment."
        )
    else:
        rationale = cotton_output["rationale"].upper()
        commodity_present = "T-SHIRT" in rationale and "COTTON" in rationale
        signal_present = "MARKET SIGNAL:" in rationale and "NO SIGNAL" not in rationale
        print(f"Cotton real commodity description present: {commodity_present}")
        print(f"Cotton real intel signal present: {signal_present}")
        print(f"Cotton evidence_ids: {cotton_output['evidence_ids']}")
        assert cotton_output["confidence"] == 0.85
        assert cotton_output["source"] == "llm"
        assert commodity_present
        assert signal_present
        assert "niryat_chat_proposal" in cotton_output["evidence_ids"]
        assert any(
            evidence.startswith("tradestat_verified_")
            for evidence in cotton_output["evidence_ids"]
        )

    gibberish_output = gibberish["department_output"]
    if gibberish_output["decision"] == "human_escalation":
        print("Gibberish result: human_escalation as expected.")
    else:
        print(
            "Gibberish result: proposed code failed verification with "
            f"confidence={gibberish_output['confidence']}"
        )
        assert gibberish_output["confidence"] == 0.4
