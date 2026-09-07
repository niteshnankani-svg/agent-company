from graph import MAX_RETRIES, route_after_verifier


for retry_count in [0, 1, 2, MAX_RETRIES, MAX_RETRIES + 2]:
    state = {"verifier_result": "pass", "retry_count": retry_count}
    assert route_after_verifier(state) == "done", (
        f"pass at retry_count={retry_count} should always be done"
    )
print("Case 1 (pass -> done at any retry_count): PASS")

_DEPT_TO_HEAD = {
    "finance": "finance_head",
    "hr_complaints": "hr_head",
    "backend_ticketing": "backend_head",
    "export_compliance": "export_head",
    "legal_compliance": "legal_head",
}
for department, head in _DEPT_TO_HEAD.items():
    for retry_count in range(MAX_RETRIES):
        state = {
            "verifier_result": "fail",
            "retry_count": retry_count,
            "active_department": department,
        }
        assert route_after_verifier(state) == head, (
            f"fail below cap for {department} should retry {head}"
        )
# Unknown/unset active_department fails closed to human review.
for active in (None, "does_not_exist"):
    state = {"verifier_result": "fail", "retry_count": 0, "active_department": active}
    assert route_after_verifier(state) == "human_escalation", (
        f"fail below cap with active_department={active!r} should escalate"
    )
assert route_after_verifier({"verifier_result": "fail", "retry_count": 0}) == (
    "human_escalation"
), "fail below cap with no active_department key should escalate"
print("Case 2 (fail below cap -> retry the department that ran): PASS")

for retry_count in [MAX_RETRIES, MAX_RETRIES + 1, MAX_RETRIES + 5]:
    state = {
        "verifier_result": "fail",
        "retry_count": retry_count,
        "active_department": "backend_ticketing",
    }
    assert route_after_verifier(state) == "human_escalation", (
        f"fail at/above cap at retry_count={retry_count} should escalate"
    )
print("Case 3 (fail at/above cap -> human_escalation): PASS")

print(
    "All cap logic cases pass, independent of API availability, auth status, or network."
)
