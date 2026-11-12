"""/ready: which departments can do their job right now."""
import json

import httpx
import pytest

import ready
import runtime as runtime_module
import services

URLS = {
    "HR_AGENT_QUERY_URL": "https://hr.internal.example/query",
    "TICKETING_URL": "https://tickets.internal.example/ticket",
    "NIRYAT_CHAT_URL": "https://niryat.internal.example/chat",
    "NIRYAT_TRADEDATA_URL": "https://niryat.internal.example/tradedata",
    "LEGAL_QUERY_URL": "https://legal.internal.example/query",
}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("AGENT_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_KEYS", "console:client:" + "k" * 30)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-value-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-value-not-real")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("READY_CACHE_SECONDS", "0")
    for k, v in URLS.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def fake_services(handler):
    seen = []
    services.set_transport(httpx.MockTransport(lambda r: seen.append(str(r.url)) or handler(r)))
    return seen


@pytest.fixture
def client(env):
    pytest.importorskip("fastapi")
    runtime_module.reset_runtime()
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app)


def test_everything_reachable_is_ready(client):
    fake_services(lambda r: httpx.Response(200, json={}))
    r = client.get("/ready")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ready"
    assert {d: v["status"] for d, v in body["departments"].items()} == {
        "finance": "ok", "hr_complaints": "ok", "backend_ticketing": "ok", "export_compliance": "ok", "legal_compliance": "ok"}
    assert body["dependencies"] == {"checkpoint_db": "ok", "auth": "configured", "router_key": "configured",
                                    "anthropic_key": "configured", "answer_cache": "off"}


def test_a_down_service_makes_only_its_department_unreachable_and_the_api_degraded_not_unready(client):
    def handler(request):
        if "hr.internal" in str(request.url):
            raise httpx.ConnectError("[Errno 111] refused", request=request)
        return httpx.Response(200, json={})
    fake_services(handler)
    r = client.get("/ready")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "degraded"
    assert body["departments"]["hr_complaints"] == {"status": "unreachable", "services": {"HR_AGENT_QUERY_URL": "unreachable"}}
    assert body["departments"]["legal_compliance"]["status"] == "ok"


def test_a_5xx_is_unhealthy_and_a_4xx_still_means_reachable(client):
    fake_services(lambda r: httpx.Response(502) if "legal" in str(r.url) else httpx.Response(404))
    d = client.get("/ready").json()["departments"]
    assert d["legal_compliance"]["status"] == "unhealthy"
    assert d["hr_complaints"]["status"] == "ok"  # POST-only services often 404/405 a GET; the server answered


def test_an_unset_address_is_not_configured(client, env):
    env.delenv("TICKETING_URL")
    fake_services(lambda r: httpx.Response(200))
    body = client.get("/ready").json()
    assert body["departments"]["backend_ticketing"]["status"] == "not_configured" and body["status"] == "degraded"


def test_export_needs_both_of_its_services(client):
    fake_services(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r)) if "tradedata" in str(r.url) else httpx.Response(200))
    e = client.get("/ready").json()["departments"]["export_compliance"]
    assert e["status"] == "unreachable" and e["services"] == {"NIRYAT_CHAT_URL": "reachable", "NIRYAT_TRADEDATA_URL": "unreachable"}


def test_a_separate_probe_address_is_used_when_given(client, env):
    env.setenv("HR_AGENT_QUERY_URL_PROBE_URL", "https://hr.internal.example/health")
    seen = fake_services(lambda r: httpx.Response(200))
    client.get("/ready")
    assert "https://hr.internal.example/health" in seen and "https://hr.internal.example/query" not in seen


def test_the_report_never_contains_an_address(client):
    fake_services(lambda r: httpx.Response(200))
    text = client.get("/ready").text
    assert "internal.example" not in text and "https://" not in text


def test_probes_are_cached_so_the_endpoint_cannot_be_used_to_hammer_services(client, env):
    env.setenv("READY_CACHE_SECONDS", "60")
    ready.reset_cache()
    seen = fake_services(lambda r: httpx.Response(200))
    client.get("/ready")
    first = len(seen)
    client.get("/ready"); client.get("/ready")
    assert len(seen) == first and first == 5  # one probe per service address, once
    ready.reset_cache()
    client.get("/ready")
    assert len(seen) == first * 2


def test_unreachable_storage_is_not_ready_and_answers_quickly(client, env):
    env.setenv("CHECKPOINT_DB_URL", "postgresql://nobody@127.0.0.1:1/none")  # nothing listens on port 1
    env.setenv("CHECKPOINT_CONNECT_TIMEOUT_SECONDS", "1")
    env.setenv("CHECKPOINT_POOL_TIMEOUT_SECONDS", "1")
    env.setenv("READY_PROBE_TIMEOUT_SECONDS", "1")
    runtime_module.reset_runtime()
    fake_services(lambda r: httpx.Response(200))
    import time
    start = time.monotonic()
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready" and r.json()["dependencies"]["checkpoint_db"] == "unavailable"
    assert time.monotonic() - start < 10, "a dead database must not make /ready hang"


def test_no_api_keys_means_not_ready(client, env):
    env.delenv("API_KEYS")
    fake_services(lambda r: httpx.Response(200))
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["dependencies"]["auth"] == "not_configured"


def test_a_missing_router_key_is_degraded(client, env):
    env.delenv("TYPESAFE_API_KEY")
    fake_services(lambda r: httpx.Response(200))
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "degraded" and r.json()["dependencies"]["router_key"] == "not_configured"


def test_a_missing_anthropic_key_is_degraded_and_never_reveals_the_value(client, env):
    env.delenv("ANTHROPIC_API_KEY")
    fake_services(lambda r: httpx.Response(200))
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "degraded"
    assert r.json()["dependencies"]["anthropic_key"] == "not_configured"
    env.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value-xyz")
    assert "sk-ant-secret-value-xyz" not in client.get("/ready").text
