from finance import finance_cache, finance_head_node
from state import new_state
from verifier import register_department_cache, verifier_node


register_department_cache("finance", finance_cache)


ar_raw_input = "Credit check applicant A"
ar_fields = {
    "age": 34,
    "income": 65000,
    "loan_amount": 25000,
    "credit_score": 610,
    "years_employed": 4,
    "missed_payments": 2,
}

ap_raw_input = "Vendor invoice thin case"
ap_fields = {
    "invoice_amount": 45000,
    "amount_vs_vendor_avg_ratio": 1.1,
    "vendor_is_first_time": False,
    "is_round_number": False,
    "is_duplicate_invoice_number": False,
    "gst_rate_pct": 5,
    "interstate": True,
    "payment_terms_days": 30,
    "days_outstanding": 52,
    "overdue": 52 > 30,
}

ap_anomaly_raw_input = "Vendor invoice anomaly case"
ap_anomaly_fields = {
    "invoice_amount": 380000,
    "amount_vs_vendor_avg_ratio": 4.2,
    "vendor_is_first_time": True,
    "is_round_number": False,
    "is_duplicate_invoice_number": False,
    "gst_rate_pct": 5,
    "interstate": True,
    "payment_terms_days": 30,
    "days_outstanding": 5,
    "overdue": 5 > 30,
}


def _run(raw_input, fields, sub_department, request_id):
    state = new_state(request_id, raw_input)
    state = finance_head_node(state, fields, sub_department)
    return verifier_node(state)


if __name__ == "__main__":
    case_a = _run(ar_raw_input, ar_fields, "ar", "verifier-a")
    print("a)", case_a["verifier_result"], case_a["audit_trail"][-1]["source"])
    assert case_a["verifier_result"] == "pass"
    assert case_a["audit_trail"][-1]["source"] == "local_model"

    case_b = _run(ap_raw_input, ap_fields, "ap", "verifier-b")
    print(
        "b)",
        case_b["department_output"]["confidence"],
        case_b["verifier_result"],
        case_b["audit_trail"][-1]["source"],
    )
    assert 0.5 <= case_b["department_output"]["confidence"] < 0.65
    assert case_b["verifier_result"] == "fail"
    assert case_b["audit_trail"][-1]["source"] == "llm"

    case_c = _run(ap_anomaly_raw_input, ap_anomaly_fields, "ap", "verifier-c")
    print("c)", case_c["verifier_result"], case_c["audit_trail"][-1]["source"])
    assert case_c["verifier_result"] == "pass"
    assert case_c["audit_trail"][-1]["source"] == "local_model"

    cache_a = finance_cache.get(ar_raw_input)
    cache_b = finance_cache.get(ap_raw_input)
    cache_c = finance_cache.get(ap_anomaly_raw_input)
    print("d)", cache_a is not None, cache_b is not None, cache_c is not None)
    assert cache_a is not None
    assert cache_b is None
    assert cache_c is not None

    attempts = 0
    retry_result = case_b
    while retry_result["verifier_result"] != "pass" and attempts < 3:
        attempts += 1
        retry_result = _run(
            ap_raw_input, ap_fields, "ap", f"verifier-e-{attempts}"
        )

    # verifier.py and finance.py do not enforce this cap. Graph wiring owns
    # the LangGraph retry edge and must force human escalation at the cap.
    cap_hit = attempts == 3 and retry_result["verifier_result"] != "pass"
    print("e) retry_cap_hit", cap_hit, "attempts", attempts)
    assert cap_hit
