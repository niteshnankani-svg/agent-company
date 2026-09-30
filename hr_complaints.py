"""HR department: Jev gates complaints from general HR/employee questions.

A specific complaint or grievance (harassment, workplace conflict,
discrimination, bullying) always goes to human review - this system does not
attempt to resolve those. A general informational question about employee
data (headcounts, department stats, etc.) is answered by the local HR agent
(via hr_agent_mcp.py -> hr-agent/query_server.py -> hr_agent.py's Text-to-SQL
+ RAG over employee data, running through Ollama on this machine).
"""
import asyncio

from typesafe_sdk import Noul, TypeSafeClient

try:
    from cache import DepartmentCache
    from state import DepartmentOutput, GraphState, log_audit
    from hr_agent_mcp import ask_hr_agent
except ImportError:
    from .cache import DepartmentCache
    from .state import DepartmentOutput, GraphState, log_audit
    from .hr_agent_mcp import ask_hr_agent

hr_cache = DepartmentCache(department="hr_complaints", similarity_threshold=0.92)
typesafe_client = TypeSafeClient()


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

    result = asyncio.run(ask_hr_agent(text))

    if "error" in result:
        output = _human_escalation_output(
            f"HR agent was unreachable or returned an error: "
            f"{result.get('detail', result['error'])}",
            0.0,
            [],
        )
    else:
        output: DepartmentOutput = {
            "decision": "answered",
            "confidence": 1.0 - complaint_probability,
            "rationale": result.get("answer", ""),
            "evidence_ids": ["hr_agent_text2sql_rag"],
            "source": "llm",
        }

    state["department_output"] = output
    state["active_department"] = "hr_complaints"
    log_audit(state, "hr_head", output["decision"], source=output["source"])
    # Never write to cache directly - only the verifier does, on pass.
    return state
