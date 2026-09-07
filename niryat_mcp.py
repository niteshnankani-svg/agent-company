"""
MCP server wrapping the deployed NiryatAI backend (Railway,
https://backend-production-a3c6.up.railway.app).

TWO TOOLS, two very different quality levels of evidence:

1. ask_export_question -> wraps /chat. Streams Server-Sent Events, and the
   answer is Claude's own conversational prose with an HS code regex-
   extracted out of markdown. This is a PROPOSAL, not verified fact.

2. get_trade_data -> wraps /tradedata, which itself POSTs to India's
   Commerce Ministry TradeStat site and parses the live HTML response
   (~7s typical, can be slower — hence the 90s timeout). Returns BOTH a
   real government commodity record AND an intel block (global-vs-India
   comparison) in one call. This is REAL government data.

CONFIDENCE HONESTY: ask_export_question never returns a numeric confidence
score from NiryatAI itself. get_trade_data's "found" flag is a much
stronger signal since it reflects whether an actual government record
exists for that code, not an LLM's guess.
"""
import re
import json
import httpx
from mcp.server.fastmcp import FastMCP

NIRYAT_CHAT_URL = "https://backend-production-a3c6.up.railway.app/chat"
NIRYAT_TRADEDATA_URL = "https://backend-production-a3c6.up.railway.app/tradedata"

mcp = FastMCP("niryat-ai")


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


@mcp.tool()
async def ask_export_question(message: str) -> dict:
    """
    Ask NiryatAI an export-compliance question. Returns the full
    conversational answer plus a best-effort extracted HS code. This is a
    PROPOSAL only — cross-check with get_trade_data before trusting the code.

    Returns: {full_answer, hs_code (or None), extraction_ok (bool)}.
    On failure: dict with an "error" key.
    """
    try:
        full_text = ""
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST", NIRYAT_CHAT_URL, json={"message": message}
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk_obj = json.loads(payload)
                        full_text += chunk_obj.get("chunk", "")
                    except json.JSONDecodeError:
                        continue

        hs_code = _extract_hs_code(full_text)
        return {
            "full_answer": full_text,
            "hs_code": hs_code,
            "extraction_ok": hs_code is not None,
        }
    except httpx.TimeoutException:
        return {"error": "timeout", "detail": "NiryatAI did not respond within 60s"}
    except httpx.HTTPStatusError as e:
        return {"error": "http_status", "status_code": e.response.status_code, "detail": e.response.text}
    except Exception as e:
        return {"error": "request_failed", "detail": str(e)}


@mcp.tool()
async def get_trade_data(hs_code: str, trade_type: str = "export", year: str = "2023-2024") -> dict:
    """
    Look up REAL Indian government export/import statistics for an HS code,
    via NiryatAI's /tradedata route (live TradeStat scrape, ~7s typical).
    Returns both a government commodity record (data) and an intel block
    (global-vs-India context) in one response.

    Use this to VERIFY a code proposed by ask_export_question: a matching
    commodity description is real corroborating evidence; an empty result
    signals the proposed code may be wrong.

    Returns: {data (list), intel (dict), found (bool)}.
    On failure: dict with an "error" key.
    """
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                NIRYAT_TRADEDATA_URL,
                json={"hs_code": hs_code, "trade_type": trade_type, "year": year},
            )
            response.raise_for_status()
            result = response.json()
            result["found"] = bool(result.get("data"))
            return result
    except httpx.TimeoutException:
        return {"error": "timeout", "detail": "TradeStat lookup did not respond within 90s"}
    except httpx.HTTPStatusError as e:
        return {"error": "http_status", "status_code": e.response.status_code, "detail": e.response.text}
    except Exception as e:
        return {"error": "request_failed", "detail": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
