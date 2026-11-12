"""Legal / compliance department: answer questions about Indian statutes from a RAG service.

The Legal RAG service (``LEGAL_QUERY_URL``) retrieves statute sections, then
generates an answer. This department returns that answer together with the
retrieved sections as evidence.

Confidence is a ``retrieval_score``: the similarity of the top retrieved section
to the question. It says the question resembles that section, not that the
generated answer is right, so it is labelled as exactly that.

Limit, stated plainly: the RAG service exposes each section's title and a short
summary, not the raw statute text, so that is the evidence text here.
"""
import asyncio
import math
from typing import Optional

from pydantic import ValidationError

from contract import Confidence, DepartmentResult, Evidence, service_error, with_feedback
from legal_client import ask_legal_question

DEPARTMENT = "legal_compliance"


def _citation_evidence(citation: dict) -> Optional[Evidence]:
    if not isinstance(citation, dict) or not citation:
        return None
    act, section = citation.get("act", "?"), citation.get("section_number", "?")
    title, summary = citation.get("title"), citation.get("summary")
    parts = [str(p) for p in (title, summary) if p]
    try:
        return Evidence(
            source=f"legal_rag:{act} Section {section}",
            text=": ".join(parts) if parts else None,
            data={k: citation[k] for k in ("act", "section_number", "title", "score") if k in citation},
        )
    except ValidationError:  # a citation with nothing usable in it
        return None


def run_legal(raw_input: str, fields: Optional[dict], feedback: Optional[str] = None) -> DepartmentResult:
    fields = fields or {}
    result = asyncio.run(
        ask_legal_question(with_feedback(fields.get("question") or raw_input, feedback), fields.get("act_filter"))
    )
    if "error" in result:
        return DepartmentResult(
            department=DEPARTMENT, status="failed", error=service_error("legal_rag", result)
        )

    citations = result.get("citations") or []
    answer = result.get("answer") or ""
    if not citations:
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="no_relevant_sections",
            answer=answer or None,
        )
    if not answer.strip():
        return DepartmentResult(
            department=DEPARTMENT, status="failed", error="legal_rag: empty answer"
        )

    evidence = [ev for ev in (_citation_evidence(c) for c in citations) if ev]
    if not evidence:
        return DepartmentResult(
            department=DEPARTMENT, status="needs_human", reason="citations_unreadable", answer=answer
        )

    top_score = citations[0].get("score") if isinstance(citations[0], dict) else None
    usable = isinstance(top_score, (int, float)) and not isinstance(top_score, bool) and math.isfinite(top_score)
    return DepartmentResult(
        department=DEPARTMENT,
        status="answered",
        answer=answer,
        evidence=evidence,
        confidence=Confidence(kind="retrieval_score", value=float(top_score)) if usable else Confidence(kind="none"),
    )
