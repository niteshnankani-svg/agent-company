"""Validated finance triage. Recommendations never execute a payment or credit decision."""
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

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
    # Cross-field checks are attached to the field that is wrong, so a
    # validation error names it ('overdue', 'is_round_number') and a follow-up
    # form can ask for just that field.
    @field_validator('is_round_number')
    @classmethod
    def round_flag_matches_amount(cls, v, info: ValidationInfo):
        amount = info.data.get('invoice_amount')
        if amount is not None and v != (amount % 10000 == 0):
            raise ValueError('round-number flag disagrees with invoice_amount')
        return v

    @field_validator('overdue')
    @classmethod
    def overdue_matches_dates(cls, v, info: ValidationInfo):
        days, terms = info.data.get('days_outstanding'), info.data.get('payment_terms_days')
        if days is not None and terms is not None and v != (days > terms):
            raise ValueError('overdue disagrees with days_outstanding and payment_terms_days')
        return v


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
    @field_validator('years_employed')
    @classmethod
    def employment_fits_age(cls, v, info: ValidationInfo):
        age = info.data.get('age')
        if age is not None and v > age - 14:
            raise ValueError('years_employed is inconsistent with age')
        return v

def credit_review(fields):
    CreditRecord.model_validate(fields)
    return {'recommendation': 'human_review', 'reason': 'credit_model_not_validated', 'default_probability': None,
            'requires_human_approval': True, 'automated_action': False,
            'basis': 'Labeled default outcomes and training provenance are required before credit-model promotion.'}

# The fields each finance path needs. Phase 3 revisits which of these the user
# must supply versus which code can derive (is_round_number, overdue).
AP_FIELDS = list(Invoice.model_fields)
AR_FIELDS = list(CreditRecord.model_fields)
