"""The service clients: addresses and timeouts from the environment, safe errors, and no network in tests."""
import asyncio
import logging

import httpx
import pytest

import hr_client
import legal_client
import niryat_client
import services
import ticketing_client

SECRET_BODY = "Traceback (most recent call last): File /srv/internal/app.py SECRET_DB_PASSWORD=hunter2"


@pytest.fixture
def transport():
    """Install a fake transport; the test sets `.handler`. Reset afterwards so nothing leaks between tests."""
    class Holder:
        handler = staticmethod(lambda request: httpx.Response(200, json={}))

    holder = Holder()
    holder.calls = []

    def route(request: httpx.Request) -> httpx.Response:
        holder.calls.append(request)
        return holder.handler(request)

    services.set_transport(httpx.MockTransport(route))
    yield holder
    services.set_transport(None)


CLIENTS = [
    ("legal", "LEGAL_QUERY_URL", "LEGAL_TIMEOUT_SECONDS", lambda: legal_client.ask_legal_question("What is s.357?", "BNS")),
    ("hr", "HR_AGENT_QUERY_URL", "HR_AGENT_TIMEOUT_SECONDS", lambda: hr_client.ask_hr_agent("How many in Sales?")),
    ("ticketing", "TICKETING_URL", "TICKETING_TIMEOUT_SECONDS", lambda: ticketing_client.ask_ticketing_agent("laptop dead")),
    ("niryat_trade", "NIRYAT_TRADEDATA_URL", "NIRYAT_TRADEDATA_TIMEOUT_SECONDS", lambda: niryat_client.get_trade_data("61091000")),
    ("niryat_chat", "NIRYAT_CHAT_URL", "NIRYAT_CHAT_TIMEOUT_SECONDS", lambda: niryat_client.ask_export_question("hs code?")),
]
IDS = [c[0] for c in CLIENTS]
param = pytest.mark.parametrize("name,url_env,timeout_env,call", CLIENTS, ids=IDS)


def run(call):
    return asyncio.run(call())


# --------------------------------------------------------------- configuration
@param
@pytest.mark.parametrize("value", [None, "", "   ", "localhost:8000", "ftp://x"])
def test_an_unset_or_invalid_address_is_a_clear_not_configured_result_and_makes_no_call(transport, monkeypatch, name, url_env, timeout_env, call, value):
    if value is None:
        monkeypatch.delenv(url_env, raising=False)
    else:
        monkeypatch.setenv(url_env, value)
    out = run(call)
    assert out["error"] == "not_configured" and url_env in out["detail"]
    assert transport.calls == []


def test_there_are_no_built_in_addresses(monkeypatch):
    """The old defaults included a terminated CloudFront host and localhost ports. None may remain."""
    import pathlib
    for module in ("services", "legal_client", "hr_client", "ticketing_client", "niryat_client"):
        text = pathlib.Path(f"{module}.py").read_text()
        for needle in ("http://", "https://d26edylmd2q5pk", "railway.app", "localhost", "54.91.16.86"):
            assert needle not in text.replace("(http://, https://)", "").replace('("http://", "https://")', ""), (module, needle)


def test_the_address_comes_from_the_environment_and_the_payloads_are_right(transport, monkeypatch):
    transport.handler = lambda r: httpx.Response(200, json={"ok": True})
    for env, url in [("LEGAL_QUERY_URL", "https://legal.example/query"), ("HR_AGENT_QUERY_URL", "https://hr.example/q"),
                     ("TICKETING_URL", "https://tickets.example/ticket"), ("NIRYAT_TRADEDATA_URL", "https://niryat.example/tradedata")]:
        monkeypatch.setenv(env, url)
    run(lambda: legal_client.ask_legal_question("q", "BNS"))
    run(lambda: hr_client.ask_hr_agent("hr q"))
    run(lambda: ticketing_client.ask_ticketing_agent("t"))
    run(lambda: niryat_client.get_trade_data("6109"))
    import json
    seen = {str(r.url): json.loads(r.content) for r in transport.calls}
    assert seen == {
        "https://legal.example/query": {"question": "q", "act_filter": "BNS"},
        "https://hr.example/q": {"question": "hr q"},
        "https://tickets.example/ticket": {"raw_text": "t"},
        "https://niryat.example/tradedata": {"hs_code": "6109", "trade_type": "export", "year": "2023-2024"},
    }


def test_timeouts_come_from_the_environment_with_documented_defaults(monkeypatch):
    for env in ("LEGAL_TIMEOUT_SECONDS", "HR_AGENT_TIMEOUT_SECONDS", "TICKETING_TIMEOUT_SECONDS"):
        monkeypatch.delenv(env, raising=False)
    assert services.timeout_seconds("LEGAL_TIMEOUT_SECONDS", 60) == 60
    monkeypatch.setenv("HR_AGENT_TIMEOUT_SECONDS", "12.5")
    assert services.timeout_seconds("HR_AGENT_TIMEOUT_SECONDS", 60) == 12.5
    for bad in ("abc", "0", "-3"):
        monkeypatch.setenv("TICKETING_TIMEOUT_SECONDS", bad)
        assert services.timeout_seconds("TICKETING_TIMEOUT_SECONDS", 30) == 30


# -------------------------------------------------------------- safe errors
@param
def test_a_timeout_reports_the_configured_limit(transport, monkeypatch, name, url_env, timeout_env, call):
    monkeypatch.setenv(url_env, "https://svc.example/x")
    monkeypatch.setenv(timeout_env, "7")
    def slow(request): raise httpx.ReadTimeout("read timed out talking to svc.example", request=request)
    transport.handler = slow
    out = run(call)
    assert out == {"error": "timeout", "detail": "no response within 7s"}


@param
def test_a_server_error_never_passes_the_remote_body_on(transport, monkeypatch, caplog, name, url_env, timeout_env, call):
    monkeypatch.setenv(url_env, "https://svc.example/x")
    transport.handler = lambda r: httpx.Response(500, text=SECRET_BODY)
    with caplog.at_level(logging.WARNING):
        out = run(call)
    assert out == {"error": "http_status", "status_code": 500, "detail": "HTTP 500"}
    assert "hunter2" not in str(out)
    assert "HTTP 500" in caplog.text  # the operator still sees it in the server log


@param
def test_a_connection_failure_reports_only_the_exception_type(transport, monkeypatch, name, url_env, timeout_env, call):
    monkeypatch.setenv(url_env, "https://svc.example/x")
    def refuse(request): raise httpx.ConnectError("[Errno 111] refused: db-internal.corp:5432 user=admin", request=request)
    transport.handler = refuse
    out = run(call)
    assert out == {"error": "unreachable", "detail": "ConnectError"}
    assert "db-internal" not in str(out)


@pytest.mark.parametrize("name,url_env,timeout_env,call", [c for c in CLIENTS if c[0] != "niryat_chat"], ids=[c[0] for c in CLIENTS if c[0] != "niryat_chat"])
def test_unreadable_or_unexpected_replies_are_bad_response(transport, monkeypatch, name, url_env, timeout_env, call):
    monkeypatch.setenv(url_env, "https://svc.example/x")
    transport.handler = lambda r: httpx.Response(200, text="<html>not json</html>")
    assert run(call)["error"] == "bad_response"
    transport.handler = lambda r: httpx.Response(200, json=["a", "list"])
    assert run(call)["error"] == "bad_response"


# ------------------------------------------------------------------ niryat
def sse(*chunks):
    lines = [f'data: {{"chunk": "{c}"}}' for c in chunks] + ["data: [DONE]"]
    return "\n".join(lines) + "\n"


def test_chat_streams_the_answer_and_extracts_the_hs_code(transport, monkeypatch):
    monkeypatch.setenv("NIRYAT_CHAT_URL", "https://niryat.example/chat")
    transport.handler = lambda r: httpx.Response(200, text=sse("Primary HS Code: ", "**61091000**", " for cotton"), headers={"content-type": "text/event-stream"})
    out = run(lambda: niryat_client.ask_export_question("cotton t-shirts"))
    assert out == {"full_answer": "Primary HS Code: **61091000** for cotton", "hs_code": "61091000", "extraction_ok": True}


def test_chat_without_a_code_is_not_an_error_but_not_extracted(transport, monkeypatch):
    monkeypatch.setenv("NIRYAT_CHAT_URL", "https://niryat.example/chat")
    transport.handler = lambda r: httpx.Response(200, text=sse("I am not sure."))
    out = run(lambda: niryat_client.ask_export_question("x"))
    assert out["extraction_ok"] is False and out["hs_code"] is None


def test_trade_data_adds_the_found_flag(transport, monkeypatch):
    monkeypatch.setenv("NIRYAT_TRADEDATA_URL", "https://niryat.example/tradedata")
    transport.handler = lambda r: httpx.Response(200, json={"data": [{"Commodity": "T-SHIRTS"}], "intel": {}})
    assert run(lambda: niryat_client.get_trade_data("6109"))["found"] is True
    transport.handler = lambda r: httpx.Response(200, json={"data": [], "intel": {}})
    assert run(lambda: niryat_client.get_trade_data("0000"))["found"] is False


CASE_A = """# Cotton T-Shirts - HS Code

**Cotton t-shirts fall under HS Chapter 61** (knitted or crocheted apparel).

**HS Code: 6109** - *T-shirts, singlets and other vests, knitted or crocheted*

- **61091000** - T-shirts of cotton (knitted)
- **61092000** - T-shirts of man-made fibres (if synthetic)
"""


@pytest.mark.parametrize("text,expected", [
    (CASE_A, "61091000"),
    ("## Primary HS Code: **61091000**\n\n**HS Code:** 61091000", "61091000"),
    ("HS Code: 610910", "610910"),
    ("the code is **6109**", None),  # four digits is a chapter heading, not a code we accept
    ("no code here", None),
])
def test_hs_code_extraction(text, expected):
    assert niryat_client._extract_hs_code(text) == expected
