"""Client for the HR agent (Text-to-SQL + RAG over employee data), at ``HR_AGENT_QUERY_URL``.

The service is a separate project. Expected reply: ``{answer, evidence}``. On failure returns a dict
with an ``error`` key (see services.py). Timeout: ``HR_AGENT_TIMEOUT_SECONDS`` (default 60; it was
280 when the agent ran a local Ollama model, and is much lower now that it uses a hosted model).
"""
from services import post_json


async def ask_hr_agent(question: str) -> dict:
    return await post_json("hr_agent", "HR_AGENT_QUERY_URL", {"question": question}, "HR_AGENT_TIMEOUT_SECONDS", 60.0)
