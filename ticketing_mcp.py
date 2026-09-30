"""
MCP server wrapping the deployed Multi_agent_ticketing_system backend
(EC2 behind CloudFront, https://d26edylmd2q5pk.cloudfront.net).

Unlike NiryatAI (a proposal to cross-check) or the legal RAG backend (a
citation to ground), this agent actually RESOLVES the ticket: it classifies
into finance/backend/internal/general and, unless it escalates, returns a
real Claude-generated answer. needs_human/escalation_reason are the ticket's
own verdict on whether that answer is trustworthy enough to use as-is.
"""
import httpx
from mcp.server.fastmcp import FastMCP

TICKETING_URL = "https://d26edylmd2q5pk.cloudfront.net/ticket"

mcp = FastMCP("ticketing-agent")


@mcp.tool()
async def ask_ticketing_agent(raw_text: str) -> dict:
    """
    Ask the ticketing agent to classify and, if confident, resolve a ticket.

    Returns: {status, category, answer, needs_human, escalation_reason} on
    success (status is "answered" or "needs_human_review"; "answered" tickets
    have a real answer, "needs_human_review" tickets have a reason and
    predicted_category/confidence instead). On failure: dict with an "error"
    key.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                TICKETING_URL, json={"raw_text": raw_text}
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        return {"error": "timeout", "detail": "Ticketing agent did not respond within 30s"}
    except httpx.HTTPStatusError as e:
        return {"error": "http_status", "status_code": e.response.status_code, "detail": e.response.text}
    except Exception as e:
        return {"error": "request_failed", "detail": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
