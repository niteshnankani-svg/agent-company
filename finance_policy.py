"""Validated finance triage. Recommendations never execute a payment or credit decision."""
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Invoice(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    invoice_amount: float = Field(gt=0, le=1e10)
    amount_vs_vendor_avg_ratio: float = Field(gt=0, le=10000)
    vendor_is_first_time: bool
    is_round_number: bool
    is_duplicate_invoice_number: bool
    gst_rate_pct: float = Field(ge=0, le=100)
    interstate: bool
    payment_terms_days: int = Field(ge=0, le=3650)
    days_outstanding: int = Field(ge=0, le=36500)
    overdue: bool
    @model_validator(mode='after')
    def consistent(self):
        if self.overdue != (self.days_outstanding > self.payment_terms_days):
            raise ValueError('overdue disagrees with dates')
        if self.is_round_number != (self.invoice_amount % 10000 == 0):
            raise ValueError('round-number flag disagrees with amount')
        return self

AP_FEATURES = list(Invoice.model_fields)
AR_FEATURES = ['age', 'income', 'loan_amount', 'credit_score', 'years_employed', 'missed_payments']

def invoice_policy(fields):
    """Versioned replica of supplied synthetic-data policy, not jurisdictional law."""
    row = Invoice.model_validate(fields)
    reasons = []
    if row.is_duplicate_invoice_number: reasons.append('duplicate_invoice_number')
    if row.amount_vs_vendor_avg_ratio > 2.5: reasons.append('amount_far_above_vendor_average')
    if row.vendor_is_first_time and row.invoice_amount > 50000: reasons.append('large_first_time_vendor_invoice')
    decision = 'flag' if reasons else 'hold' if row.days_outstanding > row.payment_terms_days + 30 else 'approve'
    if decision == 'hold': reasons.append('more_than_30_days_past_terms')
    if not reasons: reasons.append('no_defined_policy_flags')
    return {'recommendation': decision, 'reasons': reasons, 'policy_version': 'ap-triage-v2', 'requires_human_approval': True, 'automated_action': False, 'confidence': None,
            'basis': 'Supplied synthetic AP triage policy; not a calibrated fraud prediction.'}

class CreditRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    age: int = Field(ge=18, le=100)
    income: float = Field(gt=0, le=1e10)
    loan_amount: float = Field(gt=0, le=1e10)
    credit_score: int = Field(ge=300, le=850)
    years_employed: int = Field(ge=0, le=85)
    missed_payments: int = Field(ge=0, le=1000)
    @model_validator(mode='after')
    def consistent(self):
        if self.years_employed > self.age-14: raise ValueError('Employment history is inconsistent with age')
        return self

def credit_review(fields):
    CreditRecord.model_validate(fields)
    return {'recommendation': 'human_review', 'reason': 'credit_model_not_validated', 'default_probability': None,
            'requires_human_approval': True, 'automated_action': False,
            'basis': 'Labeled default outcomes and training provenance are required before credit-model promotion.'}
