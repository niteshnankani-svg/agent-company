"""Backend department: trained DistilBERT classification, team routing, and human handoff.

A confident, benign prediction is routed to the team the model suggests
(``route_to_team:<team>``); it is NOT marked resolved — the assigned team still
works the ticket. Everything uncertain, Security, Access, injected, or invalid
is escalated to a human. See docs/ROUTING_REGRESSION.md.
"""
import re

# DistilBERT margin below which the class is too close to auto-route. The model's
# own accept gate is lower (~0.55); between the gate and this we still escalate.
ROUTE_MARGIN = 0.65

if not __package__:
    from state import DepartmentOutput, GraphState, log_audit
    from backend_model import predict_backend
else:
    from .state import DepartmentOutput, GraphState, log_audit
    from .backend_model import predict_backend


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
        prediction = {'status':'blocked', 'accepted':False, 'advisory_only':True}
        reason = 'invalid_ticket_input' if not valid else 'security_review'
        decision = 'human_escalation'
    else:
        prediction = predict_backend(text)
        score = prediction.get('confidence', 0.0)
        if security or (prediction.get('accepted') and prediction.get('category') == 'Security'):
            reason = 'security_incident_review'
            prediction['review_team'] = 'Security Team'
            decision = 'human_escalation'
        elif prediction.get('accepted') and prediction.get('category') == 'Access':
            reason = 'access_authorization_required'
            decision = 'human_escalation'
        elif not prediction.get('accepted'):
            reason = 'it_classification_uncertain' if prediction.get('status') == 'classified' else 'it_classifier_' + prediction['status']
            decision = 'human_escalation'
        elif score < ROUTE_MARGIN:
            reason = 'it_classification_low_margin'
            decision = 'human_escalation'
        else:
            # Confident, benign IT ticket: assign the queue the model suggests.
            # This is the routing decision a pre-BERT confidence gate made; it
            # does NOT resolve the ticket, so no resolution evidence is claimed.
            reason = None
            decision = 'route_to_team:' + prediction['suggested_team']

    if decision.startswith('route_to_team:'):
        rationale = (f"DistilBERT classified this as {prediction['category']} with score "
                     f"{prediction['confidence']:.2f} (accept gate {prediction.get('threshold', 0.0):.2f}); "
                     f"routing to {prediction['suggested_team']}. Classification only — the assigned "
                     'team resolves the ticket.')
        evidence_ids = [f"distilbert:{prediction.get('model_version')}",
                        f"it_category:{prediction['category']}",
                        f"suggested_team:{prediction['suggested_team']}"]
    else:
        rationale = 'Backend specialist review required.'
        if prediction.get('category'):
            rationale = (f"DistilBERT suggests {prediction['category']} / {prediction['suggested_team']} "
                         f"with score {prediction['confidence']:.2f}. Human review required "
                         f"({reason}); classification does not resolve the ticket.")
        if prediction.get('review_team'):
            rationale += ' Security policy requires Security Team review.'
        evidence_ids = []

    output = DepartmentOutput(decision=decision, confidence=prediction.get('confidence', 0.),
                              rationale=rationale, evidence_ids=evidence_ids, source='local_model')

    state['backend_triage'] = prediction
    state['escalation_reason'] = reason
    state["department_output"] = output
    state["active_department"] = "backend_ticketing"
    log_audit(state, "backend_head", output["decision"], source=output["source"])
    state['audit_trail'][-1].update({'model_version':prediction.get('model_version'),
                                   'it_category':prediction.get('category'), 'review_reason':reason})
    return state
