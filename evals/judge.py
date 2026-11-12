"""Optional LLM judge. It comments on answer quality; it never decides pass or fail.

Picked by whichever key is set: GEMINI_API_KEY (model: $GEMINI_MODEL) or XAI_API_KEY / GROK_API_KEY
(model: $GROK_MODEL). With neither, the eval still runs and reports its deterministic checks.
A judge failure is recorded as an error in the report, never as a verdict.
"""
import json
import os
from typing import Optional

import httpx

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4")

PROMPT = """You are commenting on one response from a multi-department request router. A person typed one sentence.

REQUEST: {raw_input}

WHAT THIS CASE TESTS: {notes}

GROUND TRUTH (independent of the system's own explanation; may be none): {ground_truth}

THE SYSTEM'S RESULT (JSON; "status" is answered / needs_human / needs_more_info / failed; "evidence" is what the answer relied on):
{response}

Judge only: is the answer (if there is one) correct and supported by its evidence, and was the choice to answer, ask a person, ask for details or fail reasonable? Do not penalise a needs_human result for being cautious.
Respond with ONLY a JSON object with these keys: {{"answer_grounded": true/false/null (null if there is no answer), "score_1_to_5": integer, "reasoning": "2-3 sentences naming any fabrication or wrong claim"}}"""


def pick_judge() -> Optional[str]:
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY"):
        return "grok"
    return None


def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def call_judge(judge: str, client: httpx.Client, prompt: str) -> dict:
    """{"ok": True, "verdict": {...}} or {"ok": False, "error": "..."}."""
    try:
        if judge == "gemini":
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60.0,
            )
            response.raise_for_status()
            return {"ok": True, "verdict": _parse(response.json()["candidates"][0]["content"]["parts"][0]["text"])}
        if judge == "grok":
            key = os.environ.get("XAI_API_KEY") or os.environ["GROK_API_KEY"]
            response = client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": GROK_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
                timeout=60.0,
            )
            response.raise_for_status()
            return {"ok": True, "verdict": _parse(response.json()["choices"][0]["message"]["content"])}
        return {"ok": False, "error": f"unknown judge {judge}"}
    except Exception as exc:  # a failed judge call is reported, never turned into a verdict
        return {"ok": False, "error": f"judge call failed: {type(exc).__name__}"}


def judge_case(judge: str, client: httpx.Client, case: dict, response: dict) -> dict:
    return call_judge(judge, client, PROMPT.format(
        raw_input=case.get("raw_input") or " / ".join(case.get("raw_inputs", [])),
        notes=case.get("notes", ""),
        ground_truth=case.get("ground_truth") or "none",
        response=json.dumps(response.get("result", {}), indent=2, ensure_ascii=False)[:6000],
    ))
