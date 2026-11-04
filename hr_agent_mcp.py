"""
MCP server wrapping the local HR agent query server (hr-agent/query_server.py
at /Users/apple/Projects/hr-agent), a local-only, no-dependency wrapper
around hr_agent.py's Text-to-SQL + RAG over employee data via Ollama.

A fresh (uncached at this layer) question chains two local LLM calls (SQL
planning, then answer generation). Measured 60-90s+ in isolation and up to
several minutes under load from this machine's other local services
(legal-rag-chatbot, agent_company itself) competing for CPU - see the
caller's own timeout/maxDuration handling.
"""
import os

import httpx
from mcp.server.fastmcp import FastMCP

# Defaults to the local hr-agent query_server; override when it runs
# anywhere a container has to reach over the network.
HR_AGENT_QUERY_URL = os.getenv(
    "HR_AGENT_QUERY_URL", "http://localhost:8090/query"
)

mcp = FastMCP("hr-agent")


@mcp.tool()
async def ask_hr_agent(question: str) -> dict:
    """
    Ask the HR agent a question about employee/HR data. It plans and runs a
    read-only SQL query grounded in retrieved records, then writes an answer.

    Returns: {answer, evidence} on success. On failure: dict with an "error"
    key.
    """
    try:
        async with httpx.AsyncClient(timeout=280.0) as client:
            response = await client.post(
                HR_AGENT_QUERY_URL, json={"question": question}
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        return {"error": "timeout", "detail": "HR agent did not respond within 120s"}
    except httpx.HTTPStatusError as e:
        return {"error": "http_status", "status_code": e.response.status_code, "detail": e.response.text}
    except Exception as e:
        return {"error": "request_failed", "detail": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
