"""Backend department: the deployed ticketing agent (via ticketing_mcp.py)
classifies and, when confident, resolves the ticket directly.

Input validation, PII scrubbing, and injection/security-keyword screening all
happen locally first, unchanged, regardless of which agent answers - these
are checks on the raw text itself, not on the classifier. Only text that
passes them ever reaches the ticketing agent. Whatever it can't confidently
resolve is escalated to a human, using its own needs_human/escalation_reason
verdict rather than a locally re-derived confidence gate.
"""
import asyncio
import re

if not __package__:
    from state import DepartmentOutput, GraphState, log_audit
    from ticketing_mcp import ask_ticketing_agent
else:
    from .state import DepartmentOutput, GraphState, log_audit
    from .ticketing_mcp import ask_ticketing_agent


def backend_head_node(state: GraphState) -> GraphState:
    raw = state['raw_input']
    valid = isinstance(raw, str) and bool(raw.strip()) and len(raw)<=8000 and '\x00' not in raw
    text = raw.strip() if valid else ''
    text = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '<EMAIL>', text)
    text = re.sub(r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', '<PHONE>', text)
    text = re.sub(r'\b(?:\d[ -]*?){13,16}\b', '<CARD>', text)
    injection = re.search(r'ignore\s+(all\s+)?(previous|prior|system)|system\s*prompt|reveal\s+.*secret|bypass\s+.*(auth|guard)', text, re.I)
    security = re.search(r'\b(malware|ransomware|phishing|breach|botnet|ddos|intrusion|keylogger)\b|compromised\s+credentials|unauthorized\s+access|suspicious\s+|brute\s+force', text, re.I)

    if not valid or injection:
        result = None
        reason = 'invalid_ticket_input' if not valid else 'security_review'
        decision = 'human_escalation'
        confidence = 0.0
        rationale = 'Backend specialist review required.'
        evidence_ids: list[str] = []
    elif security:
        result = None
        reason = 'security_incident_review'
        decision = 'human_escalation'
        confidence = 0.0
        rationale = 'Security-related keywords detected; Security Team review required.'
        evidence_ids = []
    else:
        result = asyncio.run(ask_ticketing_agent(text))

        if 'error' in result:
            reason = 'ticketing_agent_unreachable'
            decision = 'human_escalation'
            confidence = 0.0
            rationale = (
                'The ticketing agent was unreachable or returned an error: '
                f"{result.get('detail', result['error'])}"
            )
            evidence_ids = []
        elif result.get('needs_human') or result.get('status') == 'needs_human_review':
            reason = result.get('escalation_reason') or result.get('reason') or 'ticketing_agent_escalated'
            decision = 'human_escalation'
            confidence = result.get('confidence', 0.0)
            category = result.get('category') or result.get('predicted_category')
            rationale = (
                f"Ticketing agent escalated this ticket ({reason})"
                + (f"; predicted category: {category}" if category else '')
                + '.'
            )
            evidence_ids = []
        else:
            reason = None
            decision = 'answered'
            confidence = 1.0
            rationale = result.get('answer', '')
            evidence_ids = [f"ticketing_agent:{result.get('category')}"]

    output = DepartmentOutput(decision=decision, confidence=confidence,
                              rationale=rationale, evidence_ids=evidence_ids, source='llm' if decision == 'answered' else 'human_escalation')  # type: ignore[typeddict-item]

    state['backend_triage'] = result
    state['escalation_reason'] = reason
    state["department_output"] = output
    state["active_department"] = "backend_ticketing"
    log_audit(state, "backend_head", output["decision"], source=output["source"])
    state['audit_trail'][-1].update({'ticketing_agent_category': (result or {}).get('category'), 'review_reason': reason})
    return state
