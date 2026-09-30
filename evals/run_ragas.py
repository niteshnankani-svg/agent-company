#!/usr/bin/env python3
"""RAGAS eval: standard RAG metrics (Faithfulness, Context Precision,
Factual Correctness) on the two genuinely RAG-shaped departments - Legal
(BERT retrieval + LLM generation over indexed statute text) and HR
(embedding retrieval + Text-to-SQL + LLM generation over employee data).

Honest scope limits, stated up front rather than glossed over:
  - Legal's "retrieved_contexts" are the citation `summary` fields the
    legal-rag-chatbot API returns - short LLM-generated summaries of each
    retrieved section, not the raw indexed passage text (the API doesn't
    expose that over the wire). Good enough for Faithfulness/Context
    Precision as an approximation; not the raw chunks a stricter RAGAS
    setup would use.
  - HR's "retrieved_contexts" are extracted from the SQL evidence JSON
    hr_agent.py already appends to its own answer text (parsed back out
    here, not fabricated).
  - Finance/Export/Backend are not included: finance never resolves
    anything, export's evidence is a government-data verification step
    rather than classic retrieved passages, and backend's evidence is
    another agent's own already-generated answer, not raw context.

Needs GEMINI_API_KEY (same one evals/run_evals.py uses) - see that
script's docstring for how to set it.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from ragas.llms import llm_factory
from ragas.metrics.collections import ContextPrecisionWithReference, FactualCorrectness, Faithfulness

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

CASES = [
    {
        "id": "legal-section-357",
        "raw_input": "What is Section 357 of BNS about?",
        "reference": "Section 357 of the Bharatiya Nyaya Sanhita 2023 covers breach of a lawful contract to attend on or supply the wants of a helpless person (someone helpless due to youth, unsoundness of mind, or disease/bodily weakness). Punishment: imprisonment up to three months, or a fine up to five thousand rupees, or both.",
    },
    {
        "id": "legal-section-37",
        "raw_input": "What does Section 37 of BNS say about right of private defence?",
        "reference": "Section 37 of the Bharatiya Nyaya Sanhita 2023 lists the acts against which there is no right of private defence.",
    },
    {
        "id": "hr-sales-headcount",
        "raw_input": "How many employees are in the Sales department?",
        "reference": "There are 1035 employees in the Sales department, per Employee_Performance_Dataset.csv.",
    },
    {
        "id": "hr-it-headcount",
        "raw_input": "How many employees are in the IT department?",
        "reference": "There are 974 employees in the IT department, per Employee_Performance_Dataset.csv.",
    },
]


def call_backend(client: httpx.Client, backend_url: str, raw_input: str) -> dict:
    response = client.post(
        f"{backend_url}/request",
        json={"request_id": "ragas-" + os.urandom(4).hex(), "raw_input": raw_input},
        timeout=280.0,
    )
    response.raise_for_status()
    return response.json()


def extract_hr_contexts(resp: dict) -> tuple[list[str], str]:
    rationale = resp.get("rationale", "")
    answer, sep, evidence = rationale.rpartition("\n\nEvidence: ")
    if not sep:
        return [], rationale
    try:
        evidence_obj = json.loads(evidence)
        return [json.dumps(evidence_obj)], answer
    except json.JSONDecodeError:
        return [evidence], answer


def get_legal_contexts_direct(client: httpx.Client, question: str) -> list[str]:
    response = client.post(
        "http://localhost:8000/query",
        json={"question": question, "act_filter": None},
        timeout=90.0,
    )
    response.raise_for_status()
    citations = response.json().get("citations", [])
    return [f"{c['section_number']} ({c['title']}): {c['summary']}" for c in citations]


async def run():
    # Gemini's OpenAI-compatible endpoint + a real AsyncOpenAI client, rather
    # than litellm.LiteLLM (sync-only - ragas's metrics require agenerate(),
    # which raises on a synchronous client).
    async_client = AsyncOpenAI(
        api_key=os.environ["GEMINI_API_KEY"],
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    # max_tokens raised well above the default: gemini-3.8-flash is a
    # "thinking" model that spends much of a small budget on internal
    # reasoning before the actual structured output, which was truncating
    # ragas's NLI verdict step (instructor.IncompleteOutputException).
    llm = llm_factory(GEMINI_MODEL, provider="openai", client=async_client, max_tokens=4096)

    faithfulness = Faithfulness(llm=llm)
    context_precision = ContextPrecisionWithReference(llm=llm)
    factual_correctness = FactualCorrectness(llm=llm)

    results = []
    with httpx.Client() as client:
        for case in CASES:
            print(f"[{case['id']}] calling backend...", flush=True)
            resp = call_backend(client, "http://localhost:8080", case["raw_input"])

            if case["id"].startswith("legal-"):
                contexts = get_legal_contexts_direct(client, case["raw_input"])
                answer = resp.get("rationale", "")
            else:
                contexts, answer = extract_hr_contexts(resp)

            row = {"case": case, "response": resp, "answer": answer, "contexts": contexts}

            if not contexts or not answer:
                row["error"] = "missing contexts or answer - likely escalated, skipping metrics"
                results.append(row)
                continue

            print(f"[{case['id']}] scoring faithfulness...", flush=True)
            row["faithfulness"] = (await faithfulness.ascore(
                user_input=case["raw_input"], response=answer, retrieved_contexts=contexts
            )).value

            print(f"[{case['id']}] scoring context precision...", flush=True)
            row["context_precision"] = (await context_precision.ascore(
                user_input=case["raw_input"], reference=case["reference"], retrieved_contexts=contexts
            )).value

            print(f"[{case['id']}] scoring factual correctness...", flush=True)
            row["factual_correctness"] = (await factual_correctness.ascore(
                response=answer, reference=case["reference"]
            )).value

            results.append(row)

    return results


def write_report(results: list[dict], out_path: Path) -> None:
    lines = ["# RAGAS eval run", "", f"- **Run at**: {datetime.now(timezone.utc).isoformat()}",
              f"- **Judge/LLM**: gemini/{GEMINI_MODEL} (via Gemini's OpenAI-compatible endpoint, openai.AsyncOpenAI)", ""]
    lines.append(
        "**Scope note**: only Legal and HR are included - the two departments "
        "with a genuine retrieve-then-generate shape. Legal's contexts are "
        "citation summaries (not raw indexed passage text - the API doesn't "
        "expose that). HR's contexts are the SQL evidence JSON the agent "
        "already returns. See this script's docstring for the full rationale."
    )
    lines.append("")

    scored = [r for r in results if "faithfulness" in r]
    if scored:
        lines.append(f"- **Avg faithfulness**: {sum(r['faithfulness'] for r in scored) / len(scored):.3f}")
        lines.append(f"- **Avg context precision**: {sum(r['context_precision'] for r in scored) / len(scored):.3f}")
        lines.append(f"- **Avg factual correctness**: {sum(r['factual_correctness'] for r in scored) / len(scored):.3f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for r in results:
        case = r["case"]
        lines.append(f"## {case['id']}")
        lines.append("")
        lines.append(f"**Question**: {case['raw_input']}")
        lines.append(f"**Reference**: {case['reference']}")
        lines.append("")
        lines.append(f"**Answer**: {r['answer'][:600]}")
        lines.append("")
        lines.append(f"**Retrieved contexts** ({len(r['contexts'])}):")
        for c in r["contexts"]:
            lines.append(f"- {c[:300]}")
        lines.append("")
        if "error" in r:
            lines.append(f"*{r['error']}*")
        else:
            lines.append(f"- **Faithfulness**: {r['faithfulness']:.3f}")
            lines.append(f"- **Context precision**: {r['context_precision']:.3f}")
            lines.append(f"- **Factual correctness**: {r['factual_correctness']:.3f}")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"\nWrote {out_path}")


def main():
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    results = asyncio.run(run())
    write_report(results, Path(__file__).resolve().parent / "ragas_eval.md")


if __name__ == "__main__":
    main()
