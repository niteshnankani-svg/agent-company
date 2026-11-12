"""Client for the Legal RAG service (BNS, BNSS, BSA, DPDP Act 2023), at ``LEGAL_QUERY_URL``.

Returns ``{answer, citations}`` on success; each citation has act, section_number, title, summary
and a retrieval score. On failure returns a dict with an ``error`` key (see services.py).
Timeout: ``LEGAL_TIMEOUT_SECONDS`` (default 60).
"""
from typing import Optional

from services import post_json


async def ask_legal_question(question: str, act_filter: Optional[str] = None) -> dict:
    return await post_json(
        "legal_rag", "LEGAL_QUERY_URL", {"question": question, "act_filter": act_filter},
        "LEGAL_TIMEOUT_SECONDS", 60.0,
    )
