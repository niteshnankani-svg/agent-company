#!/usr/bin/env python3
"""Optional RAGAS scoring (Faithfulness, Context Precision, Factual Correctness) for the two departments that retrieve
then generate: Legal and HR. YOU run this; it is not part of CI and needs extra packages (evals/requirements.txt).

    python evals/run_ragas.py --api-url https://your-api.example --api-key "$EVAL_API_KEY"

The "retrieved contexts" are the evidence the API returns with each answer (result.evidence). For Legal that is each
section's title and summary, not the raw statute text, which the Legal service does not expose, so Faithfulness is an
approximation. Only `answered` results can be scored; anything else is skipped and reported as such.

Needs GEMINI_API_KEY (Gemini through its OpenAI-compatible endpoint). NOT RUN in this repo's development: there was no
key, so treat the first run as the test of this script.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

CASES = [
    {"id": "legal-section-357", "raw_input": "What is Section 357 of BNS about?",
     "reference": "Section 357 of the Bharatiya Nyaya Sanhita 2023 covers breach of a lawful contract to attend on or supply the wants of a helpless person (helpless through youth, unsoundness of mind, or disease or bodily weakness). Punishment: imprisonment up to three months, or a fine up to five thousand rupees, or both."},
    {"id": "legal-section-37", "raw_input": "What does Section 37 of BNS say about right of private defence?",
     "reference": "Section 37 of the Bharatiya Nyaya Sanhita 2023 lists the acts against which there is no right of private defence."},
    {"id": "hr-sales-headcount", "raw_input": "How many employees are in the Sales department?",
     "reference": "There are 1035 employees in the Sales department, per the HR dataset."},
    {"id": "hr-it-headcount", "raw_input": "How many employees are in the IT department?",
     "reference": "There are 974 employees in the IT department, per the HR dataset."},
]


def contexts_from(response: dict) -> tuple[list[str], str]:
    """(retrieved contexts, answer) taken from an API response; empty if it was not an answered result."""
    result = response.get("result") or {}
    if result.get("status") != "answered":
        return [], ""
    contexts = []
    for item in result.get("evidence", []):
        if item.get("text"):
            contexts.append(item["text"])
        elif item.get("data"):
            contexts.append(json.dumps(item["data"], ensure_ascii=False))
    return contexts, result.get("answer") or ""


def average(rows: list[dict], key: str) -> Optional[float]:
    values = [r[key] for r in rows if key in r]
    return sum(values) / len(values) if values else None


async def run(api_url: str, api_key: Optional[str]) -> list[dict]:
    # Imported here so the helpers above work without these packages installed.
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import ContextPrecisionWithReference, FactualCorrectness, Faithfulness

    client = AsyncOpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    llm = llm_factory(GEMINI_MODEL, provider="openai", client=client, max_tokens=4096)  # a thinking model needs room
    faithfulness, precision, correctness = Faithfulness(llm=llm), ContextPrecisionWithReference(llm=llm), FactualCorrectness(llm=llm)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    rows = []
    with httpx.Client(timeout=280.0) as http:
        for case in CASES:
            print(f"[{case['id']}] calling the API...", flush=True)
            reply = http.post(f"{api_url.rstrip('/')}/request", headers=headers, json={"request_id": "ragas-" + os.urandom(4).hex(), "raw_input": case["raw_input"]})
            reply.raise_for_status()
            contexts, answer = contexts_from(reply.json())
            row = {"case": case, "answer": answer, "contexts": contexts}
            if not contexts or not answer:
                row["skipped"] = f"not an answered result with evidence (status: {(reply.json().get('result') or {}).get('status')})"
                rows.append(row)
                continue
            row["faithfulness"] = (await faithfulness.ascore(user_input=case["raw_input"], response=answer, retrieved_contexts=contexts)).value
            row["context_precision"] = (await precision.ascore(user_input=case["raw_input"], reference=case["reference"], retrieved_contexts=contexts)).value
            row["factual_correctness"] = (await correctness.ascore(response=answer, reference=case["reference"])).value
            rows.append(row)
    return rows


def render(rows: list[dict]) -> str:
    lines = ["# RAGAS eval run", "", f"- **Run at**: {datetime.now(timezone.utc).isoformat()}", f"- **LLM**: gemini/{GEMINI_MODEL}", ""]
    for key, label in (("faithfulness", "Avg faithfulness"), ("context_precision", "Avg context precision"), ("factual_correctness", "Avg factual correctness")):
        avg = average(rows, key)
        lines.append(f"- **{label}**: {avg:.3f}" if avg is not None else f"- **{label}**: no scored cases")
    lines += ["", "---", ""]
    for r in rows:
        c = r["case"]
        lines += [f"## {c['id']}", "", f"**Question**: {c['raw_input']}", f"**Reference**: {c['reference']}", "", f"**Answer**: {r['answer'][:600] or '(none)'}", ""]
        if "skipped" in r:
            lines += [f"*Skipped: {r['skipped']}*"]
        else:
            lines += [f"- Faithfulness: {r['faithfulness']:.3f}", f"- Context precision: {r['context_precision']:.3f}", f"- Factual correctness: {r['factual_correctness']:.3f}"]
        lines += ["", "---", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("AGENT_COMPANY_API_URL", "http://localhost:8080"))
    parser.add_argument("--api-key", default=os.environ.get("EVAL_API_KEY"))
    parser.add_argument("--out")
    args = parser.parse_args()
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 1
    rows = asyncio.run(run(args.api_url, args.api_key))
    out = Path(args.out) if args.out else Path(__file__).resolve().parent / "results" / ("ragas-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
