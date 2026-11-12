"""The eval harness, tested without any live service: coverage, checks, flows, report, failure-injection."""
import json
import subprocess
import sys

import httpx
import pytest

import cases as cases_module
import harness
import judge
import run_evals
import run_ragas
from cases import CASES, DEPARTMENTS, NOT_APPLICABLE, STATUSES
from harness import Api, Check, FailureServers, check_pair, check_response, coverage_gaps, failure_env, render_report, run_case


# --------------------------------------------------------------------- the cases
def test_every_department_and_status_is_covered_or_declared_impossible_with_a_reason():
    assert coverage_gaps(CASES) == []
    assert all(isinstance(why, str) and len(why) > 20 for why in NOT_APPLICABLE.values())


def test_a_cell_declared_impossible_has_no_case_pretending_otherwise():
    assert not (harness.covered_cells(CASES) & set(NOT_APPLICABLE))


def test_the_coverage_check_really_notices_a_missing_case():
    without_failed_legal = [c for c in CASES if c["id"] != "legal-service-down"]
    assert coverage_gaps(without_failed_legal) == [("legal_compliance", "failed")]
    only_answered_hr = [c for c in CASES if c.get("department") != "hr_complaints" or c["id"] == "hr-headcount-answered"]
    assert ("hr_complaints", "needs_human") in coverage_gaps(only_answered_hr)


def test_cases_are_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for c in CASES:
        assert c.get("raw_input") or c.get("raw_inputs"), c["id"]
        assert c.get("status") in STATUSES or c.get("informational"), c["id"]
        assert c.get("department") in DEPARTMENTS or c.get("department") is None, c["id"]
        assert set(c.get("break", [])) <= set(cases_module.BREAKABLE), c["id"]
        if c.get("follow_up_answers"):
            assert c.get("then", {}).get("status") in STATUSES, c["id"]
        if c.get("break"):
            assert c["status"] in ("failed", "needs_human"), c["id"]
        assert c.get("notes"), c["id"]
    assert sum(1 for c in CASES if c.get("break")) >= 5


# ------------------------------------------------------------------------ checks
def body(status="answered", **kw):
    result = {"department": "legal_compliance", "status": status, "answer": "Section 357 covers breach of contract.",
              "evidence": [{"source": "s", "text": "t", "data": None}], "reason": None, "error": None, "missing_fields": []}
    result.update(kw.pop("result", {}))
    return {"result": result, "verification": kw.pop("verification", {"status": "verified"}), **kw}


def failed(checks): return [c.name for c in checks if not c.passed]


def test_every_kind_of_expectation_can_fail_and_pass():
    exp = {"status": "answered", "department": "legal_compliance", "verification": ["verified"], "answer_contains": ["357", "breach"], "answer_not_contains": ["murder"]}
    assert failed(check_response(exp, body())) == []
    assert failed(check_response({**exp, "status": "needs_human"}, body())) == ["status"]
    assert failed(check_response({**exp, "department": "finance"}, body())) == ["department"]
    assert failed(check_response(exp, body(verification={"status": "unchecked"}))) == ["verification"]
    assert failed(check_response({**exp, "answer_contains": ["358"]}, body())) == ["answer contains '358'"]
    assert failed(check_response({**exp, "answer_not_contains": ["breach"]}, body())) == ["answer does not contain 'breach'"]


def test_reason_error_and_missing_field_expectations():
    assert failed(check_response({"status": "needs_human", "reasons": ["a", "b"]}, body("needs_human", result={"reason": "b"}))) == []
    assert failed(check_response({"status": "needs_human", "reasons": ["a"]}, body("needs_human", result={"reason": "z"}))) == ["reason"]
    err = body("failed", result={"error": "legal_rag: unreachable (ConnectError)", "answer": None, "evidence": []})
    assert failed(check_response({"status": "failed", "error_starts_with": "legal_rag: unreachable"}, err)) == []
    assert failed(check_response({"status": "failed", "error_starts_with": "hr_agent:"}, err)) == ["error says why"]
    more = body("needs_more_info", result={"missing_fields": ["a", "b"], "answer": None, "evidence": []})
    assert failed(check_response({"missing_fields_include": ["a"], "missing_fields_exclude": ["c"]}, more)) == []
    assert failed(check_response({"missing_fields_include": ["z"], "missing_fields_exclude": ["b"]}, more)) == ["asks for 'z'", "does not ask for 'b'"]


def test_an_answered_result_without_evidence_fails():
    b = body(result={"evidence": [{"source": "s", "text": None, "data": None}]})
    assert "evidence present" in failed(check_response({"status": "answered"}, b))


@pytest.mark.parametrize("leak", [
    'Traceback (most recent call last): boom', 'File "/srv/app/graph.py", line 3', "[Errno 111] refused", "password=hunter2", "api_key: sk-abcdef123456",
])
def test_internals_in_any_response_fail_every_case(leak):
    b = body(result={"error": leak})
    assert "nothing internal leaked" in failed(check_response({}, b))


def test_pair_checks():
    a, b = body(result={"answer": "Section 357 is about X"}), body(result={"answer": "Section 358 is about Y"})
    exp = {"each_answer_contains": ["357", "358"], "answers_differ": True}
    assert failed(check_pair(exp, [a, b])) == []
    assert failed(check_pair(exp, [a, a])) == ["answer mentions '358'", "the two answers differ"]


# ------------------------------------------------------------------ running cases
def make_api(handler, key="k" * 30):
    return Api("http://api.test", key, transport=httpx.MockTransport(handler))


def ok(payload):
    return lambda request: httpx.Response(200, json=payload)


def case(**kw):
    base = {"id": "c", "raw_input": "hello", "status": "answered", "department": "legal_compliance", "notes": "n"}
    base.update(kw)
    return base


def test_a_passing_case_sends_the_key_and_the_sentence():
    seen = []
    def handler(request):
        seen.append((request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200, json=body())
    result = run_case(make_api(handler), case())
    assert result.outcome == "PASS" and seen[0][0] == "Bearer " + "k" * 30
    assert seen[0][1]["raw_input"] == "hello" and "follow_up" not in seen[0][1]


def test_a_failing_check_is_a_fail_not_an_error():
    result = run_case(make_api(ok(body("needs_human", result={"reason": "x"}))), case())
    assert result.outcome == "FAIL"


@pytest.mark.parametrize("status,needle", [(401, "rejected the key"), (503, "503"), (500, "HTTP 500")])
def test_a_call_that_cannot_be_made_is_ERROR_never_a_pass(status, needle):
    result = run_case(make_api(lambda r: httpx.Response(status, json={"detail": "x"})), case())
    assert result.outcome == "ERROR" and needle in result.error


def test_an_unreachable_api_and_a_non_json_reply_are_errors():
    def refuse(request): raise httpx.ConnectError("db-internal.corp refused", request=request)
    r = run_case(make_api(refuse), case())
    assert r.outcome == "ERROR" and "ConnectError" in r.error and "db-internal" not in r.error
    assert run_case(make_api(lambda r: httpx.Response(200, text="<html>")), case()).outcome == "ERROR"


def test_the_follow_up_flow_submits_the_answers_with_what_was_collected():
    form = {"result": {"department": "finance", "status": "needs_more_info", "answer": None, "evidence": [],
                       "missing_fields": ["vendor_is_first_time"], "reason": "missing_fields", "error": None,
                       "follow_up": {"sub_department": "ap", "collected": {"invoice_amount": 45000}, "fields": []}}}
    final = body("needs_human", result={"department": "finance", "reason": "payment_requires_human_approval", "answer": "recommendation: approve"})
    sent = []
    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(200, json=final if "follow_up" in payload else form)
    c = case(department="finance", status="needs_more_info", missing_fields_include=["vendor_is_first_time"],
             follow_up_answers={"vendor_is_first_time": False},
             then={"status": "needs_human", "reasons": ["payment_requires_human_approval"], "answer_contains": ["recommendation"]})
    result = run_case(make_api(handler), c)
    assert result.outcome == "PASS" and [s.label for s in result.steps] == ["request", "follow-up (answers submitted)"]
    assert sent[1]["follow_up"] == {"department": "finance", "sub_department": "ap", "fields": {"invoice_amount": 45000, "vendor_is_first_time": False}}


def test_a_follow_up_case_whose_first_answer_is_not_a_form_fails_instead_of_passing_silently():
    c = case(follow_up_answers={"a": 1}, then={"status": "answered"})
    result = run_case(make_api(ok(body())), c)
    assert result.outcome == "FAIL" and any(ch.name == "form was offered" and not ch.passed for s in result.steps for ch in s.checks)


def test_pair_cases_send_both_sentences_and_compare():
    answers = iter(["Section 357 text", "Section 358 text"])
    def handler(request):
        return httpx.Response(200, json=body(result={"answer": next(answers)}))
    c = case(raw_input=None, raw_inputs=["q357", "q358"], pair={"each_answer_contains": ["357", "358"], "answers_differ": True})
    assert run_case(make_api(handler), c).outcome == "PASS"
    same = case(raw_input=None, raw_inputs=["a", "b"], pair={"answers_differ": True})
    assert run_case(make_api(ok(body())), same).outcome == "FAIL"


def test_informational_cases_are_never_scored():
    r = run_case(make_api(ok(body("failed", result={"error": "x", "answer": None}))), case(informational=True, status=None, department=None))
    assert r.outcome == "INFO"


# ------------------------------------------------------------------------ report
def test_the_report_shows_the_matrix_failures_and_errors_and_never_the_key():
    key = "SECRET-KEY-" + "z" * 20
    good = run_case(make_api(ok(body()), key), case(id="good"))
    bad = run_case(make_api(ok(body("needs_human", result={"reason": "x"})), key), case(id="bad", department="hr_complaints"))
    err = run_case(make_api(lambda r: httpx.Response(401, json={}), key), case(id="err"))
    text = render_report([good, bad, err], {"run_at": "now", "api": "http://api.test", "judge": None})
    assert "1 passed, 1 failed, 1 could not run" in text
    assert "## FAIL: bad" in text and "## ERROR: err" in text and "COULD NOT RUN" in text
    assert "[ ] status: expected answered, got needs_human" in text
    assert "| legal_compliance | 1/2 (1 error) |" in text and "| hr_complaints | 0/1 |" in text
    assert "n/a" in text and "Finance never answers" in text
    assert key not in text


# ---------------------------------------------------------- failure-injection servers
def test_failure_env_breaks_only_the_named_services_and_forces_authentication():
    base = {"LEGAL_QUERY_URL": "https://real.example/q", "HR_AGENT_QUERY_URL": "https://hr.example/q", "AGENT_AUTH_DISABLED": "true",
            "CHECKPOINT_DB_URL": "postgresql://prod", "TYPESAFE_API_KEY": "keep-me"}
    env = failure_env(base, ["LEGAL_QUERY_URL"], "thekey", "/tmp/x.sqlite")
    assert env["LEGAL_QUERY_URL"] == "http://127.0.0.1:9/" and env["HR_AGENT_QUERY_URL"] == "https://hr.example/q"
    assert "AGENT_AUTH_DISABLED" not in env and env["API_KEYS"] == "eval:client:thekey"
    assert env["CHECKPOINT_DB_PATH"] == "/tmp/x.sqlite" and "CHECKPOINT_DB_URL" not in env  # never touches a real database
    assert env["TYPESAFE_API_KEY"] == "keep-me" and base["LEGAL_QUERY_URL"] == "https://real.example/q"  # input not mutated
    with pytest.raises(ValueError):
        failure_env(base, ["DATABASE_URL"], "k", "/tmp/y")


def test_a_real_local_api_really_has_the_chosen_service_broken_and_requires_its_key():
    """Starts the real app in a subprocess. /ready is open, so it shows which services are actually unreachable."""
    with FailureServers(start_timeout=60) as servers:
        url, key = servers.url_and_key(["HR_AGENT_QUERY_URL"])
        assert servers.url_and_key(["HR_AGENT_QUERY_URL"]) == (url, key)  # one server per set, reused
        ready = httpx.get(f"{url}/ready", timeout=30).json()
        assert ready["departments"]["hr_complaints"]["services"] == {"HR_AGENT_QUERY_URL": "unreachable"}
        assert httpx.post(f"{url}/request", json={"request_id": "x", "raw_input": "hi"}, timeout=30).status_code == 401
        out = httpx.post(f"{url}/request", json={"request_id": "x", "raw_input": "hi"}, headers={"Authorization": f"Bearer {key}"}, timeout=60)
        assert out.status_code == 200 and "result" in out.json()
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"{url}/health", timeout=2)  # stopped on exit


# --------------------------------------------------------------------------- CLI
def fake_api_factory(responder):
    def factory(url, key=None, timeout=120.0):
        return Api(url, key, timeout, transport=httpx.MockTransport(responder))
    return factory


def test_dry_run_calls_nothing_and_succeeds(capsys):
    assert run_evals.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "20 case(s)" in out and "GAP" not in out and "[breaks LEGAL_QUERY_URL]" in out


def test_unknown_case_ids_are_rejected():
    assert run_evals.main(["--only", "no-such-case", "--dry-run"]) == 2


def test_a_full_run_writes_a_report_and_the_exit_code_follows_the_results(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_evals, "Api", fake_api_factory(lambda r: httpx.Response(200, json=body())))
    monkeypatch.setattr(run_evals, "pick_judge", lambda: None)
    out = tmp_path / "report.md"
    code = run_evals.main(["--only", "legal-section-357-answered", "--skip-failure-cases", "--out", str(out), "--api-key", "k"])
    assert code == 0 and "1 passed" in out.read_text()

    monkeypatch.setattr(run_evals, "Api", fake_api_factory(lambda r: httpx.Response(200, json=body("needs_human", result={"reason": "x"}))))
    assert run_evals.main(["--only", "legal-section-357-answered", "--skip-failure-cases", "--out", str(out)]) == 1
    monkeypatch.setattr(run_evals, "Api", fake_api_factory(lambda r: httpx.Response(401, json={})))
    assert run_evals.main(["--only", "legal-section-357-answered", "--skip-failure-cases", "--out", str(out)]) == 1  # ERROR is not ok


def test_failure_cases_can_be_skipped_so_no_server_is_started(monkeypatch, tmp_path):
    monkeypatch.setattr(run_evals, "Api", fake_api_factory(lambda r: httpx.Response(200, json=body())))
    monkeypatch.setattr(run_evals, "pick_judge", lambda: None)
    monkeypatch.setattr(run_evals, "FailureServers", lambda: pytest.fail("no server should start"))
    run_evals.main(["--only", "legal-service-down,legal-section-357-answered", "--skip-failure-cases", "--out", str(tmp_path / "r.md")])


# ------------------------------------------------------------------------- judge
def test_the_judge_parses_both_providers_and_records_failures_as_errors(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    gem = {"candidates": [{"content": {"parts": [{"text": '```json\n{"answer_grounded": true, "score_1_to_5": 4, "reasoning": "ok"}\n```'}]}}]}
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=gem)))
    assert judge.call_judge("gemini", c, "p")["verdict"]["score_1_to_5"] == 4
    monkeypatch.setenv("XAI_API_KEY", "x-key")
    grok = {"choices": [{"message": {"content": '{"answer_grounded": null, "score_1_to_5": 5, "reasoning": "fine"}'}}]}
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=grok)))
    assert judge.call_judge("grok", c, "p")["verdict"]["answer_grounded"] is None
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="secret body")))
    out = judge.call_judge("gemini", bad, "p")
    assert out == {"ok": False, "error": "judge call failed: HTTPStatusError"}  # no verdict is invented, no body leaked


def test_the_judge_key_is_sent_in_a_header_not_the_url(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"score_1_to_5": 3}'}]}}]})
    judge.call_judge("gemini", httpx.Client(transport=httpx.MockTransport(handler)), "p")
    assert "g-secret-key" not in str(seen[0].url) and seen[0].headers["x-goog-api-key"] == "g-secret-key"


def test_pick_judge(monkeypatch):
    for k in ("GEMINI_API_KEY", "XAI_API_KEY", "GROK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert judge.pick_judge() is None
    monkeypatch.setenv("GROK_API_KEY", "x")
    assert judge.pick_judge() == "grok"


# ------------------------------------------------------------------------ ragas
def test_ragas_contexts_come_from_the_evidence_of_answered_results_only():
    answered = {"result": {"status": "answered", "answer": "A", "evidence": [{"source": "s", "text": "title: summary", "data": None}, {"source": "d", "text": None, "data": {"rows": [[1035]]}}]}}
    assert run_ragas.contexts_from(answered) == (["title: summary", '{"rows": [[1035]]}'], "A")
    assert run_ragas.contexts_from({"result": {"status": "needs_human", "answer": "draft", "evidence": [{"text": "x"}]}}) == ([], "")
    assert run_ragas.average([{"a": 1.0}, {"a": 0.0}, {}], "a") == 0.5 and run_ragas.average([], "a") is None
