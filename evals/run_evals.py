#!/usr/bin/env python3
"""Run the eval suite: POST every case in cases.py to the live agent_company
backend, optionally judge each result with an LLM, and write evals.md.

Judge is picked by whichever env var is set (checked in this order):
  GEMINI_API_KEY   -> Gemini (model: $GEMINI_MODEL, default gemini-2.5-flash)
  XAI_API_KEY / GROK_API_KEY -> Grok (model: $GROK_MODEL, default grok-4)

Neither is required to run this: without a key, evals.md still gets the
real transcript (routing, decision, confidence, rationale, full audit_trail)
for every case - just without a judge verdict. Never fabricates a verdict
in place of a missing key.

Usage: python3 evals/run_evals.py [--backend-url http://localhost:8080]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4")

JUDGE_PROMPT_TEMPLATE = """You are grading one response from a multi-department support-ticket router.

TICKET SENT: {raw_input}

WHAT THIS CASE IS TESTING: {notes}

EXPECTED DEPARTMENT: {expected_department}
EXPECTED RESOLUTION: {expected_resolution}
GROUND TRUTH (if any, independent of the system's own explanation): {ground_truth}

ACTUAL SYSTEM RESPONSE (raw JSON):
{actual_response}

Grade this response. Respond with ONLY a JSON object, no other text, with these exact keys:
{{
  "routing_correct": true/false/null (null if there's no single correct department for this case),
  "resolution_appropriate": true/false (did it correctly resolve vs. escalate, given the expectation and ground truth?),
  "answer_grounded": true/false/null (null if escalated - no answer to judge; for resolved cases, does the answer match ground truth / avoid fabrication?),
  "score_1_to_5": integer (overall quality: 1=badly wrong or fabricated, 3=acceptable but flawed, 5=correct and well-grounded),
  "reasoning": "2-3 sentences explaining the score, specifically calling out any fabrication, wrong routing, or inappropriate escalation decision"
}}"""


def call_backend(client: httpx.Client, backend_url: str, raw_input: str) -> dict:
    try:
        response = client.post(
            f"{backend_url}/request",
            json={"request_id": "eval-" + os.urandom(4).hex(), "raw_input": raw_input},
            timeout=280.0,
        )
        response.raise_for_status()
        return {"ok": True, "response": response.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def pick_judge():
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY"):
        return "grok"
    return None


def call_judge_gemini(client: httpx.Client, prompt: str) -> dict:
    api_key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    response = client.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_judge_json(text)


def call_judge_grok(client: httpx.Client, prompt: str) -> dict:
    api_key = os.environ.get("XAI_API_KEY") or os.environ["GROK_API_KEY"]
    response = client.post(
        "https://api.x.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": GROK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"]["content"]
    return _parse_judge_json(text)


def _parse_judge_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def call_judge(judge: str, client: httpx.Client, prompt: str) -> dict:
    try:
        if judge == "gemini":
            return {"ok": True, "verdict": call_judge_gemini(client, prompt)}
        if judge == "grok":
            return {"ok": True, "verdict": call_judge_grok(client, prompt)}
        raise ValueError(f"unknown judge {judge}")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run(backend_url: str) -> list[dict]:
    results = []
    judge = pick_judge()
    with httpx.Client() as client:
        for case in CASES:
            print(f"[{case['id']}] calling backend...", flush=True)
            backend_result = call_backend(client, backend_url, case["raw_input"])

            judge_result = None
            if judge and backend_result["ok"]:
                print(f"[{case['id']}] calling judge ({judge})...", flush=True)
                prompt = JUDGE_PROMPT_TEMPLATE.format(
                    raw_input=case["raw_input"],
                    notes=case["notes"],
                    expected_department=case["expected_department"],
                    expected_resolution=case["expected_resolution"],
                    ground_truth=case["ground_truth"],
                    actual_response=json.dumps(backend_result["response"], indent=2),
                )
                judge_result = call_judge(judge, client, prompt)

            results.append({
                "case": case,
                "backend_result": backend_result,
                "judge_result": judge_result,
            })
    return results, judge


def write_report(results: list[dict], judge: str | None, backend_url: str, out_path: Path) -> None:
    lines = []
    lines.append("# agent_company eval run")
    lines.append("")
    lines.append(f"- **Run at**: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- **Backend**: {backend_url}")
    lines.append(f"- **Judge**: {judge or 'none - GEMINI_API_KEY/XAI_API_KEY/GROK_API_KEY not set, transcript only'}")
    lines.append(f"- **Cases run**: {len(results)}")
    lines.append("")

    scored = [r for r in results if r["judge_result"] and r["judge_result"]["ok"]]
    if scored:
        scores = [r["judge_result"]["verdict"]["score_1_to_5"] for r in scored]
        lines.append(f"- **Average score**: {sum(scores) / len(scores):.2f} / 5 (over {len(scored)} judged cases)")
        routing_checked = [r for r in scored if r["judge_result"]["verdict"]["routing_correct"] is not None]
        if routing_checked:
            routing_ok = sum(1 for r in routing_checked if r["judge_result"]["verdict"]["routing_correct"])
            lines.append(f"- **Routing correct**: {routing_ok}/{len(routing_checked)}")
        resolution_ok = sum(1 for r in scored if r["judge_result"]["verdict"]["resolution_appropriate"])
        lines.append(f"- **Resolution decision appropriate**: {resolution_ok}/{len(scored)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for r in results:
        case = r["case"]
        lines.append(f"## {case['id']}")
        lines.append("")
        lines.append(f"**Ticket**: {case['raw_input']}")
        lines.append("")
        lines.append(f"**Expected**: department=`{case['expected_department']}`, resolution=`{case['expected_resolution']}`")
        if case["ground_truth"]:
            lines.append(f"**Ground truth**: {case['ground_truth']}")
        lines.append("")
        lines.append(f"**Testing**: {case['notes']}")
        lines.append("")

        if not r["backend_result"]["ok"]:
            lines.append(f"**BACKEND CALL FAILED**: {r['backend_result']['error']}")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue

        resp = r["backend_result"]["response"]
        lines.append("**Actual response**:")
        lines.append("```json")
        lines.append(json.dumps(resp, indent=2))
        lines.append("```")
        lines.append("")

        if r["judge_result"] is None:
            lines.append("*(no judge configured)*")
        elif not r["judge_result"]["ok"]:
            lines.append(f"**JUDGE CALL FAILED**: {r['judge_result']['error']}")
        else:
            v = r["judge_result"]["verdict"]
            lines.append(f"**Judge verdict**: score {v['score_1_to_5']}/5")
            lines.append(f"- routing_correct: {v['routing_correct']}")
            lines.append(f"- resolution_appropriate: {v['resolution_appropriate']}")
            lines.append(f"- answer_grounded: {v['answer_grounded']}")
            lines.append(f"- reasoning: {v['reasoning']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"\nWrote {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", default="http://localhost:8080")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "evals.md"))
    args = parser.parse_args()

    results, judge = run(args.backend_url)
    write_report(results, judge, args.backend_url, Path(args.out))


if __name__ == "__main__":
    main()
