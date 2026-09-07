"""Conservative legal-compliance department head."""

import asyncio
import re

try:
    from state import DepartmentOutput, GraphState, log_audit
    from legal_rag_mcp import ask_legal_question
except ImportError:
    from state import DepartmentOutput, GraphState, log_audit
    from .legal_rag_mcp import ask_legal_question


_CITATION_PATTERN = re.compile(
    r"\[([A-Za-z]+(?:\s[A-Za-z]+)?),\s*(Section\s+\d+(?:\([0-9]+\))?)\]"
)


def _extract_primary_citation(answer_text: str) -> tuple[str, str] | None:
    """Extract the first section citation explicitly made by the answer.

    This follows the generated text's order because the answer's own
    citation is substantively more relevant to its decision than the highest
    similarity retrieval result, which can be textually similar but wrong.
    Returns None when the answer does not contain the expected citation form.
    """
    match = _CITATION_PATTERN.search(answer_text)
    if match:
        return match.group(1), match.group(2)
    return None


def _finish(state: GraphState, output: DepartmentOutput) -> GraphState:
    state["department_output"] = output
    state["active_department"] = "legal_compliance"
    log_audit(state, "legal_head", output["decision"], source=output["source"])
    return state


def legal_head_node(state: GraphState) -> GraphState:
    # Fall back to raw_input when the free-text path supplies no structured
    # field, so a bare {request_id, raw_input} request cannot 500 the API.
    department_fields = state.get("department_fields") or {}
    result = asyncio.run(
        ask_legal_question(
            department_fields.get("question") or state["raw_input"],
            department_fields.get("act_filter"),
        )
    )

    if "error" in result:
        return _finish(
            state,
            DepartmentOutput(
                decision="human_escalation",
                confidence=0.0,
                rationale=(
                    "The Legal RAG backend was unreachable or returned an "
                    f"error: {result.get('detail', result['error'])}"
                ),
                evidence_ids=[],
                source="human_escalation",  # type: ignore[typeddict-item]
            ),
        )

    citations = result["citations"]
    if not citations:
        return _finish(
            state,
            DepartmentOutput(
                decision="human_escalation",
                confidence=0.0,
                rationale="No relevant legal sections found for this question",
                evidence_ids=[],
                source="human_escalation",  # type: ignore[typeddict-item]
            ),
        )

    top_citation = citations[0]
    top_score = top_citation["score"]
    # Legal's confidence is the one genuinely real, unmodified similarity
    # score in this system. Respect it as-is rather than gatekeeping it with
    # an invented department-level threshold that live testing showed is not
    # trustworthy with only two calibration points. The shared verifier owns
    # the ambiguous-band and grounding-check decision.
    primary = _extract_primary_citation(result["answer"])
    if primary:
        act, section = primary
        decision = f"{act} {section}"
        rationale_note = ""
    else:
        # Fallback for an answer that declines to cite a section or uses an
        # unexpected format; retain the old behavior but flag its limitation.
        decision = (
            f"{top_citation['act']} {top_citation['section_number']}: "
            f"{top_citation['title']}"
        )
        rationale_note = (
            " [Note: no explicit section citation found in the generated "
            "answer text — decision falls back to the highest-similarity "
            "retrieved section, which may not reflect the answer's actual "
            "content; treat with extra caution.]"
        )

    output = DepartmentOutput(
        decision=decision,
        confidence=top_score,
        rationale=result["answer"] + rationale_note,
        evidence_ids=[
            f"legal_section_{citation['section_number']}"
            for citation in citations
        ],
        source="llm",
    )
    return _finish(state, output)
