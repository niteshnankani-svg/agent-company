"""Finance department: deterministic, fail-closed triage. No external service.

Policy (unchanged from before the rebuild): finance never executes a payment or a
credit decision. Every valid finance request ends in ``needs_human`` with the
policy's recommendation attached for the reviewer. The legacy credit model stays
quarantined; nothing here loads it.

Inputs: ``sub_department`` ("ar" = customer credit, "ap" = supplier invoice) and
``fields``. Missing or invalid fields produce ``needs_more_info`` naming exactly
which fields to ask for; they never crash and never guess.
"""
from typing import Optional

from pydantic import ValidationError

from contract import DepartmentResult, Evidence
from finance_policy import (
    AP_FIELDS,
    AR_FIELDS,
    credit_review,
    invoice_policy,
)

DEPARTMENT = "finance"
SUB_DEPARTMENTS = ("ar", "ap")


def _more_info(missing: list[str], reason: str) -> DepartmentResult:
    return DepartmentResult(
        department=DEPARTMENT,
        status="needs_more_info",
        missing_fields=missing,
        reason=reason,
    )


def _check_fields(fields: dict, required: list[str], validate) -> tuple[Optional[dict], Optional[DepartmentResult]]:
    """Return (analysis, None) when the record is valid, else (None, needs_more_info)."""
    absent = [name for name in required if name not in fields or fields[name] in (None, "")]
    if absent:
        return None, _more_info(absent, "missing_fields")
    try:
        return validate(fields), None
    except ValidationError as exc:
        bad: list[str] = []
        for err in exc.errors():
            name = str(err["loc"][0]) if err["loc"] else None
            if name and name not in bad:
                bad.append(name)
        # A record-level error with no field attached cannot be turned into a
        # question for the user; refuse rather than guess which field to ask for.
        return None, _more_info(bad or list(required), "invalid_fields")


def run_finance(raw_input: str, fields: Optional[dict], sub_department: Optional[str]) -> DepartmentResult:
    fields = fields or {}

    if sub_department not in SUB_DEPARTMENTS:
        return _more_info(["sub_department"], "finance_path_not_specified")

    if sub_department == "ar":
        analysis, problem = _check_fields(fields, AR_FIELDS, credit_review)
        if problem:
            return problem
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="credit_model_not_validated",
            answer=(
                "Credit review cannot be automated: the credit model has no labeled "
                "default evaluation or provenance yet. A qualified human must decide."
            ),
            evidence=[
                Evidence(
                    source="credit_review_policy",
                    text=analysis["basis"],
                    data={"input": fields, "analysis": analysis},
                )
            ],
        )

    analysis, problem = _check_fields(fields, AP_FIELDS, invoice_policy)
    if problem:
        return problem
    return DepartmentResult(
        department=DEPARTMENT,
        status="needs_human",
        reason="payment_requires_human_approval",
        answer=(
            f"AP policy recommendation: {analysis['recommendation']} "
            f"({', '.join(analysis['reasons'])}). Human approval required; "
            "no payment executed."
        ),
        evidence=[
            Evidence(
                source=f"ap_policy:{analysis['policy_version']}",
                text=analysis["basis"],
                data={"input": fields, "analysis": analysis},
            )
        ],
    )
