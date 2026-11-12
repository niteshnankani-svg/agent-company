"""Client for the deployed ticketing agent, at ``TICKETING_URL``.

It classifies a ticket and, when it is confident, answers it. Reply fields used: status, category,
answer, needs_human, escalation_reason. There is no default address: the old default pointed at a
terminated server. On failure returns a dict with an ``error`` key (see services.py).
Timeout: ``TICKETING_TIMEOUT_SECONDS`` (default 30).
"""
from services import post_json


async def ask_ticketing_agent(raw_text: str) -> dict:
    return await post_json("ticketing_agent", "TICKETING_URL", {"raw_text": raw_text}, "TICKETING_TIMEOUT_SECONDS", 30.0)
