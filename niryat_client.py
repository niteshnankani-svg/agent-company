"""Client for the NiryatAI service: an HS-code proposal (``/chat``) and a TradeStat lookup (``/tradedata``).

Two calls, two very different qualities of evidence:

1. ``ask_export_question`` streams Server-Sent Events from ``NIRYAT_CHAT_URL``. The answer is a model's
   conversational prose with an HS code regex-extracted out of it: a PROPOSAL, not a fact.
2. ``get_trade_data`` posts to ``NIRYAT_TRADEDATA_URL``, which scrapes India's TradeStat site. A record
   existing for a code is real government data; it does not prove the code fits a given product.

Timeouts: ``NIRYAT_CHAT_TIMEOUT_SECONDS`` (60) and ``NIRYAT_TRADEDATA_TIMEOUT_SECONDS`` (90). On failure
each returns a dict with an ``error`` key (see services.py).
"""
import json
import re
from typing import Optional

from services import (
    error_from_exception, http_client, not_configured, post_json, service_url, timeout_seconds,
)


def _extract_hs_code(full_text: str) -> str | None:
    """Best-effort extraction of a primary HS code from NiryatAI's prose.

    Patterns are ordered most-specific-first: the first three require
    explicit "HS Code" labeling (highest confidence this is THE code, not an
    unrelated number); the last two are broader fallbacks for responses that
    state the code as a bolded number without that exact label (confirmed
    necessary from a real response phrased as a bolded bullet-list item).
    """
    patterns = [
        r"Primary HS Code:\s*\*\*?\*?(\d{6,8})",
        r"HS Code:\*\*\s*(\d{6,8})",
        r"HS Code[:\s]+(\d{6,8})",
        r"\*\*(\d{8})\*\*",
        r"\*\*(\d{6})\*\*",
    ]
    for pattern in patterns:
        match = re.search(pattern, full_text)
        if match:
            return match.group(1)
    return None


async def ask_export_question(message: str) -> dict:
    """Returns {full_answer, hs_code (or None), extraction_ok} or an error dict."""
    url = service_url("NIRYAT_CHAT_URL")
    if url is None:
        return not_configured("NIRYAT_CHAT_URL")
    timeout = timeout_seconds("NIRYAT_CHAT_TIMEOUT_SECONDS", 60.0)
    try:
        full_text = ""
        async with http_client(timeout) as client:
            async with client.stream("POST", url, json={"message": message}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        full_text += json.loads(payload).get("chunk", "")
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:
        return error_from_exception(exc, timeout, "niryat_chat")
    hs_code = _extract_hs_code(full_text)
    return {"full_answer": full_text, "hs_code": hs_code, "extraction_ok": hs_code is not None}


async def get_trade_data(hs_code: str, trade_type: str = "export", year: str = "2023-2024") -> dict:
    """Returns {data (list), intel (dict), found (bool)} or an error dict."""
    result = await post_json(
        "niryat_tradedata", "NIRYAT_TRADEDATA_URL",
        {"hs_code": hs_code, "trade_type": trade_type, "year": year},
        "NIRYAT_TRADEDATA_TIMEOUT_SECONDS", 90.0,
    )
    if "error" not in result:
        result["found"] = bool(result.get("data"))
    return result
