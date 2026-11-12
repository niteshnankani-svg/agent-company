"""A department whose service is down returns `failed` with a clear reason. It never crashes or hangs the graph."""
import threading
import time

import httpx
import pytest

import departments
import hr_complaints
import orchestrator
import services
from contract import DepartmentResult
from helpers import invoke_graph
from state import new_state

SECRET = "db-internal.corp:5432 user=admin password=hunter2"


@pytest.fixture
def down(monkeypatch):
    """Every service address is set, and every call fails with an exception whose message is sensitive."""
    for env in ("HR_AGENT_QUERY_URL", "TICKETING_URL", "NIRYAT_CHAT_URL", "NIRYAT_TRADEDATA_URL", "LEGAL_QUERY_URL"):
        monkeypatch.setenv(env, "https://svc.example/x")
    monkeypatch.setattr(hr_complaints, "complaint_probability", lambda t: 0.01)

    def refuse(request): raise httpx.ConnectError(SECRET, request=request)
    services.set_transport(httpx.MockTransport(refuse))
    yield
    services.set_transport(None)


def result(dept, text, fields=None):
    return departments.run_department(dept, text, fields)


@pytest.mark.parametrize("dept,text,service", [
    ("legal_compliance", "What is Section 357 of BNS?", "legal_rag"),
    ("hr_complaints", "How many employees are in Sales?", "hr_agent"),
    ("backend_ticketing", "my laptop will not boot", "ticketing_agent"),
    ("export_compliance", "cotton t-shirts", "niryat_chat"),
])
def test_a_down_service_is_a_failed_result_with_a_clear_reason_and_no_internals(down, dept, text, service):
    r = result(dept, text)
    assert r.status == "failed" and r.error == f"{service}: unreachable (ConnectError)"
    assert "hunter2" not in r.error and "db-internal" not in r.error


@pytest.mark.parametrize("dept,text,envs,service", [
    ("legal_compliance", "What is Section 357?", ["LEGAL_QUERY_URL"], "legal_rag"),
    ("hr_complaints", "How many in Sales?", ["HR_AGENT_QUERY_URL"], "hr_agent"),
    ("backend_ticketing", "laptop dead", ["TICKETING_URL"], "ticketing_agent"),
    ("export_compliance", "cotton t-shirts", ["NIRYAT_CHAT_URL"], "niryat_chat"),
])
def test_an_unset_address_is_failed_naming_the_variable(monkeypatch, dept, text, envs, service):
    for e in ("HR_AGENT_QUERY_URL", "TICKETING_URL", "NIRYAT_CHAT_URL", "NIRYAT_TRADEDATA_URL", "LEGAL_QUERY_URL"):
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setattr(hr_complaints, "complaint_probability", lambda t: 0.01)
    r = result(dept, text)
    assert r.status == "failed" and r.error.startswith(f"{service}: not_configured") and envs[0] in r.error


def test_export_with_only_the_check_service_down_goes_to_a_person_not_a_crash(monkeypatch):
    monkeypatch.setenv("NIRYAT_CHAT_URL", "https://niryat.example/chat")
    monkeypatch.setenv("NIRYAT_TRADEDATA_URL", "https://niryat.example/tradedata")

    def handler(request):
        if "chat" in str(request.url):
            return httpx.Response(200, text='data: {"chunk": "HS Code: **61091000**"}\ndata: [DONE]\n')
        raise httpx.ReadTimeout("slow", request=request)
    services.set_transport(httpx.MockTransport(handler))
    try:
        r = result("export_compliance", "cotton t-shirts")
    finally:
        services.set_transport(None)
    assert r.status == "needs_human" and r.reason == "tradestat_verification_unavailable" and "timeout" in r.answer


def test_the_whole_graph_carries_on_when_a_service_is_down(down, monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    final = invoke_graph(new_state("d1", "What is Section 357 of BNS?"))
    r = DepartmentResult.from_state(final["department_result"])
    assert r.status == "failed" and "legal_rag: unreachable" in r.error
    assert [e["node"] for e in final["audit_trail"]][-1] == "cache_store"  # ran to the end, no crash


# ------------------------------------------------------------------- hung departments
def test_a_department_that_never_finishes_is_abandoned_and_reported_not_waited_for(monkeypatch):
    release = threading.Event()
    monkeypatch.setenv("DEPARTMENT_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setitem(departments.RUNNERS, "legal_compliance", lambda *a: release.wait(30))
    start = time.monotonic()
    r = departments.run_department("legal_compliance", "q", {})
    elapsed = time.monotonic() - start
    release.set()
    assert r.status == "failed" and r.error == "timed_out: legal_compliance did not finish within 0.3s"
    assert elapsed < 3


def test_a_hung_department_does_not_hang_the_graph(monkeypatch):
    release = threading.Event()
    monkeypatch.setenv("DEPARTMENT_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setitem(departments.RUNNERS, "legal_compliance", lambda *a: release.wait(30))
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    start = time.monotonic()
    final = invoke_graph(new_state("h1", "law question"))
    release.set()
    assert DepartmentResult.from_state(final["department_result"]).error.startswith("timed_out") and time.monotonic() - start < 5


def test_an_exception_raised_by_a_department_is_not_mistaken_for_a_timeout(monkeypatch):
    def raises(*a): raise TimeoutError("socket timed out talking to db-internal.corp")
    monkeypatch.setitem(departments.RUNNERS, "legal_compliance", raises)
    r = departments.run_department("legal_compliance", "q", {})
    assert r.error == "unexpected_error: TimeoutError" and "db-internal" not in r.error


def test_the_default_deadline_exceeds_export_s_two_service_timeouts(monkeypatch):
    monkeypatch.delenv("DEPARTMENT_TIMEOUT_SECONDS", raising=False)
    assert departments.department_timeout() > services.timeout_seconds("NIRYAT_CHAT_TIMEOUT_SECONDS", 60) + services.timeout_seconds("NIRYAT_TRADEDATA_TIMEOUT_SECONDS", 90)


# ------------------------------------------------------ no exception text in 500 responses
def test_a_500_carries_an_incident_id_not_the_exception_text(monkeypatch, caplog):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import api
    import runtime as runtime_module

    runtime_module.reset_runtime()
    graph = runtime_module.get_runtime().graph
    monkeypatch.setattr(graph, "invoke", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"boom {SECRET}")), raising=False)
    with caplog.at_level("ERROR"):
        r = TestClient(api.app).post("/request", json={"request_id": "x", "raw_input": "hello there"})
    assert r.status_code == 500
    assert "hunter2" not in r.text and "db-internal" not in r.text and "boom" not in r.text
    incident = r.json()["detail"].split("Incident ")[1].rstrip(".")
    assert len(incident) == 10 and incident in caplog.text and "hunter2" in caplog.text  # the operator can find it in the log


def test_an_error_outside_the_request_handler_is_also_generic(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import api

    def explode(): raise RuntimeError(f"explode {SECRET}")
    monkeypatch.setattr(api, "get_runtime", explode)
    r = TestClient(api.app, raise_server_exceptions=False).get("/reviews")
    assert r.status_code == 500 and "hunter2" not in r.text and "explode" not in r.text and "Incident" in r.text
