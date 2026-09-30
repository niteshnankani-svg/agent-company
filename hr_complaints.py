"""HR department: Jev gates complaints from general HR/employee questions.

A specific complaint or grievance (harassment, workplace conflict,
discrimination, bullying) always goes to human review - this system does not
attempt to resolve those. A general informational question about employee
data (headcounts, department stats, etc.) is answered by hr-agent
(https://github.com/.../hr-agent), a local Text-to-SQL + RAG tool over the
employee dataset, running via Ollama on this machine.
"""
import sys
import threading
from pathlib import Path

from typesafe_sdk import Noul, TypeSafeClient

try:
    from cache import DepartmentCache
    from state import DepartmentOutput, GraphState, log_audit
except ImportError:
    from .cache import DepartmentCache
    from .state import DepartmentOutput, GraphState, log_audit

hr_cache = DepartmentCache(department="hr_complaints", similarity_threshold=0.92)
typesafe_client = TypeSafeClient()

_HR_AGENT_DIR = Path("/Users/apple/Projects/hr-agent")
if str(_HR_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_HR_AGENT_DIR))

_hr_agent_state = {"conn": None, "client": None, "retriever": None}
# api.py runs each request in its own worker thread (asyncio.to_thread), so
# the shared SQLite connection + retriever below need exclusive access per
# call - sqlite3's check_same_thread=False only disables the same-thread
# check, it does not make a connection safe for concurrent use.
_hr_agent_lock = threading.Lock()


def _get_hr_agent():
    # Built lazily (not at import time) so importing this module never
    # requires Ollama to be reachable - only actually answering a question does.
    if _hr_agent_state["conn"] is None:
        from hr_agent import CSV_PATH, Ollama, Retriever, build_database

        _hr_agent_state["conn"] = build_database(CSV_PATH)
        _hr_agent_state["client"] = Ollama()
        _hr_agent_state["retriever"] = Retriever(
            _hr_agent_state["conn"], _hr_agent_state["client"]
        )
    return (
        _hr_agent_state["conn"],
        _hr_agent_state["client"],
        _hr_agent_state["retriever"],
    )


def _human_escalation_output(rationale: str, confidence: float, evidence_ids: list[str]) -> DepartmentOutput:
    # state.DepartmentOutput predates this department and does not include the
    # human_escalation source literal; the runtime contract requires this safe
    # source for a failed or gated path.
    return {
        "decision": "human_escalation",
        "confidence": confidence,
        "rationale": rationale,
        "evidence_ids": evidence_ids,
        "source": "human_escalation",  # type: ignore[typeddict-item]
    }


def hr_head_node(state: GraphState) -> GraphState:
    department_fields = state.get("department_fields") or {}
    text = department_fields.get("complaint_text") or state["raw_input"]

    cached = hr_cache.get(state["raw_input"])
    if cached:
        state["department_output"] = cached
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", cached["decision"], source="cache")
        return state

    response = typesafe_client.system_one(
        state=text,
        questions={
            "is_complaint": Noul(
                instructions=(
                    "Is this a specific employee complaint or grievance "
                    "(harassment, workplace conflict, discrimination, "
                    "bullying, an issue with a specific person or incident) "
                    "that needs human review, as opposed to a general "
                    "informational question about HR or employee data?"
                ),
            ),
        },
    )
    complaint_probability = response.nouls["is_complaint"].noul

    if complaint_probability >= 0.5:
        output = _human_escalation_output(
            "Jev classified this as an employee complaint or grievance "
            "requiring human review.",
            complaint_probability,
            ["jev_complaint_gate"],
        )
        state["department_output"] = output
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", output["decision"], source="human_escalation")
        return state

    try:
        from hr_agent import answer_question

        with _hr_agent_lock:
            conn, client, retriever = _get_hr_agent()
            answer = answer_question(conn, text, client, retriever)
        output: DepartmentOutput = {
            "decision": "answered",
            "confidence": 1.0 - complaint_probability,
            "rationale": answer,
            "evidence_ids": ["hr_agent_text2sql_rag"],
            "source": "llm",
        }
    except Exception as error:
        output = _human_escalation_output(
            f"hr-agent could not answer this question: {error}",
            0.0,
            [],
        )
        state["department_output"] = output
        state["active_department"] = "hr_complaints"
        log_audit(state, "hr_head", output["decision"], source="human_escalation")
        return state

    state["department_output"] = output
    state["active_department"] = "hr_complaints"
    log_audit(state, "hr_head", output["decision"], source="llm")
    # Never write to cache directly - only the verifier does, on pass.
    return state
