"""Export / compliance department: propose an HS code, then check it exists in TradeStat.

Two services, two very different kinds of evidence:

1. NiryatAI ``/chat``: an LLM's prose. This is a *proposal*.
2. NiryatAI ``/tradedata``: a live lookup in India's TradeStat records. A hit means
   a government record exists for that code. It does NOT yet confirm the record's
   commodity matches the product asked about; that comparison is the verifier's
   job (Phase 4), which is why the commodity text is carried as evidence.

Only a TradeStat hit gives ``answered``. A code that is unverified, missing from
TradeStat, or a lookup that could not run is ``needs_human`` with the proposal
attached. Confidence is ``none``: there is no real number to report.

No caching, by design: a replayed HS code without a fresh TradeStat check is not safe.
"""
import asyncio
from typing import Optional

from contract import DepartmentResult, Evidence, service_error
from niryat_mcp import ask_export_question, get_trade_data

DEPARTMENT = "export_compliance"


def run_export(raw_input: str, fields: Optional[dict]) -> DepartmentResult:
    product = (fields or {}).get("product_description") or raw_input

    proposal = asyncio.run(ask_export_question(f"What is the HS code for {product}?"))
    if "error" in proposal:
        return DepartmentResult(
            department=DEPARTMENT, status="failed", error=service_error("niryat_chat", proposal)
        )

    chat_text = proposal.get("full_answer") or ""
    hs_code = proposal.get("hs_code")
    if not proposal.get("extraction_ok") or not hs_code:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="hs_code_not_identified",
            evidence=[Evidence(source="niryat_chat", text=chat_text)] if chat_text.strip() else [],
        )

    chat_evidence = Evidence(source="niryat_chat", text=chat_text or f"Proposed HS code {hs_code}")
    verification = asyncio.run(get_trade_data(hs_code))

    if "error" in verification:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="tradestat_verification_unavailable",
            answer=f"Proposed HS code {hs_code} (not verified: {service_error('tradestat', verification)}).",
            evidence=[chat_evidence],
        )

    if verification.get("found") is not True:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="hs_code_not_in_tradestat",
            answer=f"Proposed HS code {hs_code}, but TradeStat has no record for it.",
            evidence=[chat_evidence],
        )

    record = verification["data"][0]
    commodity = record.get("Commodity", "")
    signal = (verification.get("intel") or {}).get("signal")
    answer = f"HS code {hs_code}: {commodity}." if commodity else f"HS code {hs_code}."
    if signal:
        answer += f" Market signal: {signal}"
    return DepartmentResult(
        department=DEPARTMENT,
        status="answered",
        answer=answer,
        evidence=[
            chat_evidence,
            Evidence(
                source=f"tradestat:{hs_code}",
                text=f"TradeStat commodity: {commodity}" if commodity else None,
                data=record,
            ),
        ],
    )
