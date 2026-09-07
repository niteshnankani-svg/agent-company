from finance import finance_cache, finance_head_node
from state import new_state


ar_raw_input = "Customer credit check for loan applicant #4521"
ar_fields = {
    "age": 34,
    "income": 65000,
    "loan_amount": 25000,
    "credit_score": 610,
    "years_employed": 4,
    "missed_payments": 2,
}

ap_raw_input = "Vendor invoice payment for Bhilwara Textiles"
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

ap_anomaly_raw_input = "Vendor invoice payment for new supplier"
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


def print_result(label, state):
    print(label)
    print("department_output:", state["department_output"])
    print("audit source:", state["audit_trail"][-1]["source"])


if __name__ == "__main__":
    ar_state = finance_head_node(
        new_state("ar-1", ar_raw_input), ar_fields, "ar"
    )
    print_result("a) AR", ar_state)

    finance_cache.set(ar_raw_input, ar_state["department_output"])
    cache_state = finance_head_node(
        new_state("ar-2", ar_raw_input), ar_fields, "ar"
    )
    print_result("b) Cache", cache_state)

    ap_state = finance_head_node(
        new_state("ap-1", ap_raw_input), ap_fields, "ap"
    )
    print_result("c) AP normal", ap_state)

    ap_anomaly_state = finance_head_node(
        new_state("ap-2", ap_anomaly_raw_input), ap_anomaly_fields, "ap"
    )
    print_result("d) AP anomaly", ap_anomaly_state)
