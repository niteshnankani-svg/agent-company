from graph import MAX_RETRIES, build_graph, build_initial_state


app = build_graph()


def print_result(label, result):
    print(f"\n{label}")
    print("department_output:", result["department_output"])
    print("retry_count:", result["retry_count"])
    print("audit_trail:", result["audit_trail"])


if __name__ == "__main__":
    ar_fields = {
        "age": 34,
        "income": 65000,
        "loan_amount": 25000,
        "credit_score": 610,
        "years_employed": 4,
        "missed_payments": 2,
    }
    case_a = app.invoke(
        build_initial_state(
            "graph-a",
            "Customer credit check for loan applicant #4521",
            ar_fields,
            "ar",
        )
    )
    print_result("a) Confident AR", case_a)
    assert case_a["verifier_result"] == "pass"
    assert case_a["retry_count"] == 0
    assert len(case_a["audit_trail"]) == 3
    assert [entry["node"] for entry in case_a["audit_trail"]] == [
        "orchestrator",
        "finance_head",
        "verifier",
    ]

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
    case_b = app.invoke(
        build_initial_state(
            "graph-b",
            "Vendor invoice thin case via graph",
            ap_fields,
            "ap",
        )
    )
    print_result("b) Thin AP retry cap", case_b)
    finance_entries = [
        entry for entry in case_b["audit_trail"] if entry["node"] == "finance_head"
    ]
    verifier_entries = [
        entry for entry in case_b["audit_trail"] if entry["node"] == "verifier"
    ]
    assert case_b["retry_count"] <= MAX_RETRIES
    assert len(finance_entries) >= 1
    assert len(verifier_entries) >= 1
    assert case_b["department_output"]["decision"] in ("human_escalation",)

    unsupported_state = build_initial_state(
        "graph-c",
        "A request outside the supported departments",
        {},
        None,
    )
    unsupported_state["llm_budget_remaining"] = 0
    case_c = app.invoke(unsupported_state)
    print_result("c) Unsupported department", case_c)
    assert case_c["department_output"]["decision"] == "unsupported_department"
    assert "legal" in case_c["department_output"]["rationale"]
