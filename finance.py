"""Finance department: from a plain sentence plus a follow-up form to a fail-closed recommendation.

Flow for one request:

1. Work out the path: ``ar`` (a customer's credit or loan) or ``ap`` (a supplier invoice).
   Taken from the caller if given, otherwise proposed from the sentence by the model and
   verified by code (extraction.py). If it is still unknown, ask.
2. Fill the record from three sources: what the user typed on the form, what the model
   found in the sentence AND the code verified, and values the code derives (finance_input.py).
3. If anything is missing or unusable, return ``needs_more_info`` with a ``follow_up`` the UI
   renders as a form. Nothing is guessed.
4. When the record is complete and valid, apply the policy. Finance never executes a payment
   or a credit decision: a valid request ends in ``needs_human`` with the recommendation and
   the record (and where each value came from) as evidence. The legacy credit model stays
   quarantined; nothing here loads it.

On a follow-up round (``follow_up=True``) the model is not asked again (the form already asked),
unless the user has only just picked the path on that form.
"""
from typing import Optional

from pydantic import ValidationError

from contract import DepartmentResult, Evidence, FollowUp
from extraction import FieldDef, extract_fields
from finance_input import (
    ALL_DEFS, AP_DEFS, AR_DEFS, BLAME, DEFS, DERIVED, PATH_FIELD,
    assemble, model_record, required_fields, valid_path,
)
from finance_policy import credit_review, invoice_policy

DEPARTMENT = "finance"


def _more_info(
    path: Optional[str], names: list[str], collected: dict, problems: dict, reason: str
) -> DepartmentResult:
    specs = [ALL_DEFS[n].spec(problems.get(n)) for n in names]
    return DepartmentResult(
        department=DEPARTMENT,
        status="needs_more_info",
        missing_fields=names,
        reason=reason,
        follow_up=FollowUp(sub_department=path, collected=collected, fields=specs),
    )


def _collected(values: dict) -> dict:
    """What to send back with the form: everything understood so far except what code re-derives."""
    return {k: v for k, v in values.items() if k not in DERIVED}


def _friendly(err: dict) -> str:
    msg = str(err.get("msg", "not acceptable"))
    return msg.removeprefix("Value error, ")


def run_finance(
    raw_input: str,
    fields: Optional[dict],
    sub_department: Optional[str],
    follow_up: bool = False,
) -> DepartmentResult:
    provided = dict(fields or {})
    path = valid_path(sub_department) or valid_path(provided.get("sub_department"))

    # ---- 1 & 2: ask the model about whatever is still unknown, unless the form already asked
    extraction_error = None
    extracted_values: dict = {}
    extracted_quotes: dict = {}
    # The form already asked for everything it could, so a follow-up does not ask the model again,
    # except when the path was only just chosen on that form: the numbers in the sentence were not
    # used yet because they belong to a path nobody had picked.
    path_was_known = valid_path(sub_department) is not None
    if not (follow_up and path_was_known):
        if path is None:
            defs: list[FieldDef] = [PATH_FIELD, *AR_DEFS, *AP_DEFS]
        else:
            defs = [d for d in DEFS[path] if provided.get(d.name) in (None, "")]
        outcome = extract_fields(raw_input, defs)
        extraction_error = outcome.error
        extracted_values, extracted_quotes = outcome.values, outcome.quotes
        if path is None:
            path = valid_path(extracted_values.pop("sub_department", None))
            extracted_quotes.pop("sub_department", None)

    if path is None:
        return _more_info(
            None, [PATH_FIELD.name], {}, {},
            "extraction_unavailable" if extraction_error else "finance_path_not_specified",
        )

    # ---- 2: assemble the record
    record = assemble(path, provided, extracted_values, extracted_quotes)
    needed = required_fields(path, record.values)
    missing = [n for n in needed if n not in record.values]
    # A field the user supplied but got wrong is asked again, with the problem shown.
    for name in record.problems:
        if name in needed and name not in missing:
            missing.append(name)
    if missing:
        reason = "extraction_unavailable" if extraction_error and not record.values else (
            "invalid_fields" if record.problems else "missing_fields"
        )
        return _more_info(path, missing, _collected(record.values), record.problems, reason)

    # ---- 3: validate and apply the policy
    data = model_record(path, record.values)
    try:
        analysis = (credit_review if path == "ar" else invoice_policy)(data)
    except ValidationError as exc:
        problems: dict[str, str] = {}
        for err in exc.errors():
            name = str(err["loc"][0]) if err["loc"] else None
            if name:
                problems.setdefault(BLAME.get(name, name), _friendly(err))
        names = [n for n in problems if n in ALL_DEFS]
        if not names:  # a record-level error cannot be turned into a question
            names = needed
        collected = _collected({k: v for k, v in record.values.items() if k not in names})
        return _more_info(path, names, collected, problems, "invalid_fields")

    evidence = Evidence(
        source="credit_review_policy" if path == "ar" else f"ap_policy:{analysis['policy_version']}",
        text=analysis["basis"],
        data={"input": data, "provenance": record.provenance, "analysis": analysis},
    )
    if path == "ar":
        return DepartmentResult(
            department=DEPARTMENT,
            status="needs_human",
            reason="credit_model_not_validated",
            answer=(
                "Credit review cannot be automated: the credit model has no labeled "
                "default evaluation or provenance yet. A qualified human must decide."
            ),
            evidence=[evidence],
        )
    return DepartmentResult(
        department=DEPARTMENT,
        status="needs_human",
        reason="payment_requires_human_approval",
        answer=(
            f"AP policy recommendation: {analysis['recommendation']} "
            f"({', '.join(analysis['reasons'])}). Human approval required; "
            "no payment executed."
        ),
        evidence=[evidence],
    )
