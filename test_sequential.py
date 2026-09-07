"""Live sequential export-then-finance graph test.

Requires the same live NiryatAI network access as test_export.py. In a
sandboxed environment, export proposal or TradeStat calls may fail; that is a
network limitation, while the sequence and Finance assertions still verify
the preserved history and final-step behavior.
"""

from graph import build_graph
from state import new_state


app = build_graph()

state = new_state("seq-test-1", "Cotton t-shirt export credit check")
state["department_sequence"] = ["export_compliance", "finance"]
state["department_fields_by_dept"] = {
    "export_compliance": {
        "fields": {"product_description": "cotton t-shirts"}
    },
    "finance": {
        "fields": {
            "age": 34,
            "income": 65000,
            "loan_amount": 25000,
            "credit_score": 610,
            "years_employed": 4,
            "missed_payments": 2,
        },
        "sub_department": "ar",
    },
}

result = app.invoke(state)

print("department_history:")
print(result["department_history"])
print("final department_output:")
print(result["department_output"])
print("audit_trail:")
print(result["audit_trail"])

assert len(result["department_history"]) == 2
assert result["department_history"][0]["department"] == "export_compliance"
assert result["department_history"][1]["department"] == "finance"
assert result["department_history"][0]["output"]["decision"]
assert result["department_history"][1]["output"]["decision"] in {
    "low_risk",
    "high_risk",
}
assert result["department_output"] == result["department_history"][1]["output"]
assert [entry["node"] for entry in result["audit_trail"][:3]] == [
    "orchestrator",
    "export_head",
    "finance_head",
]
print("PASS: sequential history and final Finance output are preserved")
