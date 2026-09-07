"""Finance compatibility adapter with fail-closed model governance.
Legacy pickles are evaluated offline by Company Desk; they do not make live
credit/payment decisions. No semantic-cache replay or LLM confidence override.
"""
try:
    from finance_policy import invoice_policy, credit_review
    from state import log_audit
except ImportError:
    from .finance_policy import invoice_policy, credit_review
    from .state import log_audit

class DisabledFinanceCache:
    def get(self, key): return None
    def set(self, key, value): return None
finance_cache=DisabledFinanceCache()

def customer_side_finance(customer_fields):
    try: analysis=credit_review(customer_fields)
    except ValueError:
        analysis={'reason':'invalid_credit_record'}
    return {'decision':'human_escalation','confidence':0.0,'rationale':'Credit model quarantined: labeled default evaluation and provenance required.','evidence_ids':[],'source':'human_escalation','analysis':analysis}

def supplier_side_finance(invoice_fields):
    try:
        analysis=invoice_policy(invoice_fields)
        rationale=f"AP policy recommendation: {analysis['recommendation']}; {', '.join(analysis['reasons'])}. Human approval required; no payment executed."
    except ValueError:
        analysis={'reason':'invalid_invoice_record'}
        rationale='Invoice fields failed validation; human review required.'
    return {'decision':'human_escalation','confidence':0.0,'rationale':rationale,'evidence_ids':['ap-triage-v2'],'source':'human_escalation','analysis':analysis}

def llm_escalate_finance(prior_output,state):
    return {**prior_output,'decision':'human_escalation','confidence':0.0,'source':'human_escalation','rationale':'A qualified human must review this finance recommendation; an LLM cannot override model validation.'}

def finance_head_node(state,department_fields,sub_department):
    if sub_department=='ar':output=customer_side_finance(department_fields)
    elif sub_department=='ap':output=supplier_side_finance(department_fields)
    else:output={'decision':'human_escalation','confidence':0.0,'source':'human_escalation','rationale':'Unknown finance capability','evidence_ids':[]}
    state['department_output']=output;state['active_department']='finance'
    log_audit(state,'finance_head',output['decision'],source=output['source'])
    return state
