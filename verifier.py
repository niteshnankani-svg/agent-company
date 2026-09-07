try:
    from state import GraphState, log_audit
except ImportError:
    from .state import GraphState, log_audit


_DEPARTMENT_CACHES = {}


def register_department_cache(department: str, cache) -> None:
    _DEPARTMENT_CACHES[department] = cache


VERIFIER_PROMPT = """You are checking whether a department's decision is
supported by its own stated evidence and rationale. You do NOT re-decide the
case; only check internal consistency. Respond with EXACTLY ONE WORD: PASS or
FAIL, nothing else."""


def _rule_check(output: dict) -> str:
    if output["decision"] == "human_escalation":
        return "pass"
    if not output.get("evidence_ids"):
        return "fail"
    if output["confidence"] < 0.5:
        return "fail"
    if 0.5 <= output["confidence"] < 0.65:
        return "ambiguous"
    return "pass"


def _llm_ground_check(output: dict, state: GraphState) -> str:
    if state["llm_budget_remaining"] <= 0:
        return "fail"

    prompt_input = (
        f"decision: {output['decision']}\n"
        f"confidence: {output['confidence']:.2%}\n"
        f"rationale: {output['rationale']}\n"
        f"evidence_ids: {output['evidence_ids']}"
    )
    try:
        from anthropic import AnthropicBedrock

        client = AnthropicBedrock(aws_region="us-east-1")
        # Decremented before the attempt — a failed call still counts.
        state["llm_budget_remaining"] -= 1
        response = client.messages.create(
            model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            max_tokens=10,
            system=VERIFIER_PROMPT,
            messages=[{"role": "user", "content": prompt_input}],
        )
        verdict = response.content[0].text.strip().upper()
        return "pass" if verdict == "PASS" else "fail"
    except Exception as exc:
        print(f"Verifier LLM check skipped/failed: {exc}")
        return "fail"


def verifier_node(state: GraphState) -> GraphState:
    output = state.get("department_output")
    department = state.get("active_department")

    if output is None:
        state["verifier_result"] = "fail"
        state["retry_count"] += 1
        log_audit(state, "verifier", "fail", source="local_model")
        return state

    result = _rule_check(output)
    if result == "ambiguous":
        result = _llm_ground_check(output, state)
        source = "llm"
    else:
        source = "local_model"

    if result == "pass":
        state["verifier_result"] = "pass"
        if output.get("decision") != "human_escalation":
            cache = _DEPARTMENT_CACHES.get(department)
            if cache is not None:
                cache.set(state["raw_input"], output)
        log_audit(state, "verifier", "pass", source=source)
    else:
        state["verifier_result"] = "fail"
        state["retry_count"] += 1
        log_audit(state, "verifier", "fail", source=source)
    return state
