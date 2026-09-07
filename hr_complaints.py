import sys
from pathlib import Path

try:
    from complaint_agent import analyze_reviews, predict_sentiment
except ModuleNotFoundError:
    # The source app is currently stored outside this project. If complaint_agent.py
    # is copied into agent_company later, the direct import above takes precedence.
    _SOURCE_APP = Path("/Users/apple/ai-complaint-agent/ai-complaint-agent")
    if _SOURCE_APP.exists():
        sys.path.insert(0, str(_SOURCE_APP))
    from complaint_agent import analyze_reviews, predict_sentiment

try:
    from cache import DepartmentCache
    from state import DepartmentOutput, GraphState, log_audit
except ImportError:
    from .cache import DepartmentCache
    from .state import DepartmentOutput, GraphState, log_audit


hr_cache = DepartmentCache(
    department="hr_complaints", similarity_threshold=0.92
)


def _human_escalation_output(rationale: str, evidence_ids: list[str]) -> DepartmentOutput:
    # state.DepartmentOutput predates this department and does not include the
    # human_escalation source literal; the runtime contract requires this safe
    # source for a failed CrewAI path.
    return {
        "decision": "human_escalation",
        "confidence": 0.0,
        "rationale": rationale,
        "evidence_ids": evidence_ids,
        "source": "human_escalation",  # type: ignore[typeddict-item]
    }


def hr_head_node(state: GraphState) -> GraphState:
    # Free-text /request supplies no structured HR fields. Fall back to
    # raw_input and a neutral category so the node returns a real decision
    # instead of raising KeyError/TypeError, which api.py turns into an
    # opaque 500. Every other department head does the same (see
    # export_compliance.py / legal_compliance.py).
    department_fields = state.get("department_fields") or {}
    complaint_text = department_fields.get("complaint_text") or state["raw_input"]
    category = department_fields.get("category") or "general"
    evidence_id = f"bert_sentiment_{category}"

    cached = hr_cache.get(state["raw_input"])
    if cached:
        state["department_output"] = cached
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", cached["decision"], source="cache")
        return state

    try:
        sentiment, confidence = predict_sentiment(complaint_text, category)
    except Exception as error:
        output = _human_escalation_output(
            f"HR sentiment model unavailable ({category}): {error}", [evidence_id]
        )
        state["department_output"] = output
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", output["decision"], source="human_escalation")
        return state
    if sentiment == "positive":
        output: DepartmentOutput = {
            "decision": "not_a_complaint",
            "confidence": confidence,
            "rationale": (
                f"BERT sentiment classifier ({category}): positive, "
                "no complaint detected"
            ),
            "evidence_ids": [evidence_id],
            "source": "local_model",
        }
        state["department_output"] = output
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", output["decision"], source="local_model")
        return state

    REQUIRED_BUDGET_FOR_CREWAI = 4
    if state["llm_budget_remaining"] < REQUIRED_BUDGET_FOR_CREWAI:
        output = _human_escalation_output(
            f"Negative complaint requires CrewAI review but only "
            f"{state['llm_budget_remaining']} budget remains "
            f"(needs {REQUIRED_BUDGET_FOR_CREWAI}) — not attempted.",
            [evidence_id],
        )
        state["department_output"] = output
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", output["decision"], source="human_escalation")
        return state

    state["llm_budget_remaining"] -= REQUIRED_BUDGET_FOR_CREWAI
    try:
        report = analyze_reviews(
            [{"text": complaint_text, "category": category}]
        )
    except Exception as error:
        output = _human_escalation_output(
            f"CrewAI pipeline failed: {error}", [evidence_id]
        )
        state["department_output"] = output
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", output["decision"], source="human_escalation")
        return state

    output = {
        "decision": "requires_review",
        "confidence": confidence,
        "rationale": str(report)[:500],
        "evidence_ids": [evidence_id, "crewai_pipeline_v1"],
        "source": "llm",
    }
    state["department_output"] = output
    state["active_department"] = "hr_complaints"
    log_audit(state, "hr_head", output["decision"], source="llm")
    # Never write to cache directly — only the verifier does, on pass.
    return state
