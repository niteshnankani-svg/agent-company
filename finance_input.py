"""What finance needs from a person, and how it is assembled from a sentence plus a form.

Three sources feed one record, in this order of authority:

1. values the user typed on the follow-up form (or an API client supplied);
2. values the model proposed from the sentence AND the code verified (extraction.py);
3. values the code derives itself (a ratio, "round number", "overdue").

The user is only ever asked for what the policy actually uses and what cannot be
derived. ``gst_rate_pct`` and ``interstate`` are accepted if supplied but never asked
for, because the AP policy does not use them.
"""
from dataclasses import dataclass, field
from typing import Any, Optional

from extraction import FieldDef, coerce_value

PATH_FIELD = FieldDef(
    name="sub_department",
    label="What is this about?",
    kind="choice",
    choices={
        "ar": "A customer's credit or loan request (money owed to us)",
        "ap": "A supplier invoice (money we owe)",
    },
    extract=True,
)

AR_DEFS = [
    FieldDef("age", "Applicant's age", "integer", help="in years", extract=True),
    FieldDef("income", "Annual income (₹)", "number", extract=True),
    FieldDef("loan_amount", "Loan amount requested (₹)", "number", extract=True),
    FieldDef("credit_score", "Credit score", "integer", help="between 300 and 850", extract=True),
    FieldDef("years_employed", "Years employed", "integer", extract=True),
    FieldDef("missed_payments", "Missed payments", "integer", help="how many payments were missed", extract=True),
]

AP_DEFS = [
    FieldDef("invoice_amount", "Invoice amount (₹)", "number", extract=True),
    FieldDef("vendor_is_first_time", "Is this the first time we are paying this vendor?", "boolean"),
    FieldDef(
        "vendor_average_invoice", "What this vendor usually invoices (₹)", "number",
        help="Leave blank for a first-time vendor.", extract=True,
    ),
    FieldDef("is_duplicate_invoice_number", "Has this invoice number been used before?", "boolean"),
    FieldDef("payment_terms_days", "Payment terms (days)", "integer", extract=True),
    FieldDef("days_outstanding", "Days since the invoice date", "integer", extract=True),
]

# Accepted from API clients that send the older, complete record; never asked for.
LEGACY_DEFS = [
    FieldDef("amount_vs_vendor_avg_ratio", "Invoice amount compared with the vendor's average", "number"),
    FieldDef("is_round_number", "Is the invoice amount a round number?", "boolean"),
    FieldDef("overdue", "Is the invoice overdue?", "boolean"),
    FieldDef("gst_rate_pct", "GST rate (%)", "number"),
    FieldDef("interstate", "Is this an interstate supply?", "boolean"),
]

DEFS = {"ar": AR_DEFS, "ap": AP_DEFS}
ALL_DEFS = {d.name: d for d in [*AR_DEFS, *AP_DEFS, *LEGACY_DEFS, PATH_FIELD]}
DERIVED = {"amount_vs_vendor_avg_ratio", "is_round_number", "overdue"}
PATHS = tuple(PATH_FIELD.choices)


def valid_path(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value in PATHS else None


def required_fields(path: str, values: dict) -> list[str]:
    """Field names the user must supply, in the order the form shows them."""
    if path == "ar":
        return [d.name for d in AR_DEFS]
    names = [d.name for d in AP_DEFS if d.name != "vendor_average_invoice"]
    needs_average = values.get("vendor_is_first_time") is not True and "amount_vs_vendor_avg_ratio" not in values
    if needs_average:
        names.insert(2, "vendor_average_invoice")
    return names


@dataclass
class Assembled:
    values: dict[str, Any] = field(default_factory=dict)       # usable values, including derived ones
    provenance: dict[str, str] = field(default_factory=dict)   # where each value came from
    problems: dict[str, str] = field(default_factory=dict)     # field -> why a supplied value was unusable


def assemble(path: str, provided: dict, extracted_values: dict, extracted_quotes: dict) -> Assembled:
    """Merge form values, verified extractions and derived values into one record."""
    out = Assembled()
    allowed = {d.name: d for d in [*DEFS[path], *(LEGACY_DEFS if path == "ap" else [])]}

    for name, defn in allowed.items():
        if name in provided:
            value, problem = coerce_value(defn, provided[name])
            if problem:
                out.problems[name] = problem
            elif value is not None:
                out.values[name] = value
                out.provenance[name] = "entered by the user"
                continue
        if name not in out.problems and name in extracted_values:
            out.values[name] = extracted_values[name]
            out.provenance[name] = f"stated in the request: “{extracted_quotes.get(name, '')}”"

    if path == "ap":
        v = out.values
        if "amount_vs_vendor_avg_ratio" not in v and v.get("vendor_is_first_time") is not True:
            avg, amount = v.get("vendor_average_invoice"), v.get("invoice_amount")
            if avg and amount and avg > 0:
                v["amount_vs_vendor_avg_ratio"] = round(amount / avg, 4)
                out.provenance["amount_vs_vendor_avg_ratio"] = "derived: invoice amount ÷ vendor's usual invoice"
        if "is_round_number" not in v and "invoice_amount" in v:
            v["is_round_number"] = v["invoice_amount"] % 10000 == 0
            out.provenance["is_round_number"] = "derived from the invoice amount"
        if "overdue" not in v and "days_outstanding" in v and "payment_terms_days" in v:
            v["overdue"] = v["days_outstanding"] > v["payment_terms_days"]
            out.provenance["overdue"] = "derived from the dates"
    return out


def model_record(path: str, values: dict) -> dict:
    """The exact dict handed to the policy model (only fields it defines)."""
    if path == "ar":
        return {d.name: values[d.name] for d in AR_DEFS if d.name in values}
    keys = [
        "invoice_amount", "vendor_is_first_time", "amount_vs_vendor_avg_ratio", "is_round_number",
        "is_duplicate_invoice_number", "gst_rate_pct", "interstate", "payment_terms_days",
        "days_outstanding", "overdue",
    ]
    return {k: values[k] for k in keys if k in values}


# A policy-model field that is computed from the form maps back to the field the user can change.
BLAME = {"amount_vs_vendor_avg_ratio": "vendor_average_invoice"}
