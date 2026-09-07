"""MCP wrapper for the deployed ticket-router service.

SECURITY: The backing API currently uses plain HTTP on a raw IP address, not
HTTPS or a domain. Ticket text may contain customer PII and therefore travels
unencrypted. This is acceptable only for local/development use; before real
production traffic, place the service behind HTTPS, such as through the same
CloudFront distribution serving the frontend or an ALB.
"""

import httpx
from mcp.server.fastmcp import FastMCP


TICKET_API_URL = "http://54.91.16.86:8000/ticket"
mcp = FastMCP("ticket-router")


@mcp.tool()
async def submit_ticket(ticket_id: str, raw_text: str) -> dict:
    """Submit a ticket to the deployed multi-agent ticket router.

    The router classifies tickets into four categories: ``finance`` covers
    billing, payments, and other financial issues; ``backend`` covers
    technical or service problems; ``internal`` covers internal operational
    requests; and ``general`` covers requests outside those categories.
    Low-confidence cases may return ``needs_human=True`` for human review.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                TICKET_API_URL,
                json={"ticket_id": ticket_id, "raw_text": raw_text},
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as error:
        # A timeout could mean the service's interrupt() mechanism is blocking.
        # Live tests of confident and attempted-low-confidence cases returned
        # near-instantly with needs_human in the body, suggesting the deployed
        # API already handles interrupt() without blocking. A genuine
        # needs_human=True response was not observed, so retain this as a
        # safety net rather than proof that interrupt() causes timeouts.
        return {"error": "timeout", "detail": str(error)}
    except httpx.HTTPStatusError as error:
        return {
            "error": "http_status",
            "status_code": error.response.status_code,
            "detail": str(error),
        }
    except Exception as error:
        return {"error": "request_failed", "detail": str(error)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
