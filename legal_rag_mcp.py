"""MCP wrapper for the locally running Legal RAG backend."""

import os

import httpx
from mcp.server.fastmcp import FastMCP


# Defaults to the local legal-rag-chatbot; override when it runs anywhere
# a container has to reach over the network.
LEGAL_QUERY_URL = os.getenv("LEGAL_QUERY_URL", "http://localhost:8000/query")
mcp = FastMCP("legal-rag")


@mcp.tool()
async def ask_legal_question(
    question: str, act_filter: str | None = None
) -> dict:
    """
    Ask a legal question about Indian law (BNS, BNSS, BSA, or DPDP Act 2023),
    grounded in indexed legal text via BERT retrieval and Claude generation.

    Unlike prose-only legal services, this returns structured citations
    directly. Each citation includes its similarity score and exact section
    number from the indexed legal text.

    Args:
        question: The legal question, with a minimum length of five characters.
        act_filter: "BNS", "BNSS", "BSA", "DPDP", "ALL", or None.

    Returns:
        The generated answer and structured citations. On failure, returns a
        dict with an ``error`` key.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                LEGAL_QUERY_URL,
                json={"question": question, "act_filter": act_filter},
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as error:
        return {
            "error": "timeout",
            "detail": f"Legal RAG backend did not respond within 60s: {error}",
        }
    except httpx.HTTPStatusError as error:
        return {
            "error": "http_status",
            "status_code": error.response.status_code,
            "detail": error.response.text,
        }
    except Exception as error:
        return {"error": "request_failed", "detail": str(error)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
