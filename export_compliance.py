"""Conservative export-compliance department head."""

import asyncio

try:
    from state import DepartmentOutput, GraphState, log_audit
    from niryat_mcp import ask_export_question, get_trade_data
except ImportError:
    from .state import DepartmentOutput, GraphState, log_audit
    from .niryat_mcp import ask_export_question, get_trade_data


# Export/compliance decisions are high-stakes enough that replaying a cached
# HS code without a fresh TradeStat check is deliberately avoided. Caching is
# therefore disabled here, making this the most conservative department.


def _finish(state: GraphState, output: DepartmentOutput) -> GraphState:
    state["department_output"] = output
    state["active_department"] = "export_compliance"
    log_audit(state, "export_head", output["decision"], source=output["source"])
    return state


def export_head_node(state: GraphState) -> GraphState:
    # Fall back to raw_input when the free-text path supplies no structured
    # field, so a bare {request_id, raw_input} request cannot 500 the API.
    department_fields = state.get("department_fields") or {}
    product_description = (
        department_fields.get("product_description") or state["raw_input"]
    )

    proposal = asyncio.run(
        ask_export_question(
            f"What is the HS code for {product_description}?"
        )
    )

    if "error" in proposal:
        return _finish(
            state,
            DepartmentOutput(
                decision="human_escalation",
                confidence=0.0,
                rationale=(
                    "NiryatAI /chat was unreachable; export-compliance "
                    f"HS-code proposal could not be obtained: {proposal.get('detail', proposal['error'])}"
                ),
                evidence_ids=[],
                source="human_escalation",  # type: ignore[typeddict-item]
            ),
        )

    if proposal["extraction_ok"] is False:
        return _finish(
            state,
            DepartmentOutput(
                decision="human_escalation",
                confidence=0.0,
                rationale="NiryatAI could not identify a specific HS code for this product",
                evidence_ids=[],
                source="human_escalation",  # type: ignore[typeddict-item]
            ),
        )

    hs_code = proposal["hs_code"]
    verification = asyncio.run(get_trade_data(hs_code))

    if "error" in verification:
        output = DepartmentOutput(
            decision=hs_code,
            confidence=0.5,
            rationale=(
                f"NiryatAI proposed HS code {hs_code}. "
                "TradeStat verification could not be completed: "
                f"{verification.get('detail', verification['error'])}"
            ),
            evidence_ids=["niryat_chat_proposal"],
            source="llm",
        )
        return _finish(state, output)

    if verification["found"] is True:
        commodity = verification["data"][0]["Commodity"]
        signal = (verification.get("intel") or {}).get("signal")
        signal_text = (
            f"Market signal: {signal}"
            if signal
            else "Market signal: no signal was returned by NiryatAI intel."
        )
        # 0.85 is HIGH for this department, not equivalent to a trained
        # model's probability; TradeStat confirms the record, not certainty
        # about every product attribute.
        output = DepartmentOutput(
            decision=hs_code,
            confidence=0.85,
            rationale=(
                f"NiryatAI proposal: {proposal['full_answer']}\n"
                f"TradeStat commodity: {commodity}\n"
                f"{signal_text}"
            ),
            evidence_ids=[
                "niryat_chat_proposal",
                f"tradestat_verified_{hs_code}",
            ],
            source="llm",
        )
        return _finish(state, output)

    output = DepartmentOutput(
        decision=hs_code,
        confidence=0.4,
        rationale=(
            f"NiryatAI proposed HS code {hs_code}, but the code was not found "
            "in TradeStat records. It remains unconfirmed and requires human "
            "review for export compliance."
        ),
        evidence_ids=["niryat_chat_proposal"],
        source="llm",
    )
    return _finish(state, output)
