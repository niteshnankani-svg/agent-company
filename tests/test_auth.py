"""API-key authentication: two roles, a reviewer identity that cannot be self-declared, and fail-closed."""
import hmac
import logging

import pytest

import orchestrator
import runtime as runtime_module

CLIENT_KEY = "client-key-" + "a" * 20
REVIEWER_KEY = "reviewer-key-" + "b" * 20


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.delenv("AGENT_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_KEYS", f"console:client:{CLIENT_KEY},asha:reviewer:{REVIEWER_KEY}")
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("backend_ticketing", 0.95))
    runtime_module.reset_runtime()
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app)


def bearer(key): return {"Authorization": f"Bearer {key}"}


def ask(client, key=CLIENT_KEY):
    return client.post("/request", json={"request_id": "r", "raw_input": "this looks like phishing"}, headers=bearer(key))


# --------------------------------------------------------------------- who is let in
@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer wrong-key-" + "x" * 20}, {"Authorization": "Bearer "}, {"Authorization": "Bearer"},
    {"Authorization": f"Basic {CLIENT_KEY}"}, {"Authorization": CLIENT_KEY}, {"X-API-Key": CLIENT_KEY},
])
def test_missing_or_wrong_credentials_are_401_and_the_response_never_echoes_a_key(client, headers):
    for call in (lambda: client.post("/request", json={"request_id": "r", "raw_input": "hi"}, headers=headers),
                 lambda: client.get("/reviews", headers=headers), lambda: client.get("/runs/abc", headers=headers),
                 lambda: client.post("/reviews/abc/resume", json={"action": "reject"}, headers=headers)):
        r = call()
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert CLIENT_KEY not in r.text and REVIEWER_KEY not in r.text


def test_health_and_ready_need_no_key(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code in (200, 503)  # answers, whatever the state


def test_a_client_key_can_submit_and_look_up_but_not_review(client):
    body = ask(client).json()
    assert body["pending_review"] is True
    assert client.get(f"/runs/{body['run_id']}", headers=bearer(CLIENT_KEY)).status_code == 200
    assert client.get("/reviews", headers=bearer(CLIENT_KEY)).status_code == 403
    r = client.post(f"/reviews/{body['run_id']}/resume", json={"action": "reject"}, headers=bearer(CLIENT_KEY))
    assert r.status_code == 403
    assert client.get(f"/runs/{body['run_id']}", headers=bearer(CLIENT_KEY)).json()["pending_review"] is True  # untouched


def test_a_reviewer_key_can_do_everything(client):
    run_id = ask(client, REVIEWER_KEY).json()["run_id"]
    assert client.get("/reviews", headers=bearer(REVIEWER_KEY)).json()["count"] == 1
    assert client.post(f"/reviews/{run_id}/resume", json={"action": "reject"}, headers=bearer(REVIEWER_KEY)).status_code == 200


def test_lowercase_bearer_scheme_is_accepted(client):
    assert client.get("/reviews", headers={"Authorization": f"bearer {REVIEWER_KEY}"}).status_code == 200


# ------------------------------------------------------- the reviewer cannot be self-declared
def test_the_recorded_reviewer_is_the_keys_name_whatever_the_body_says(client):
    run_id = ask(client).json()["run_id"]
    out = client.post(f"/reviews/{run_id}/resume", json={"action": "reject", "reviewer": "the CEO", "note": "no"}, headers=bearer(REVIEWER_KEY)).json()
    last = out["audit_trail"][-1]
    assert last["reviewer"] == "asha"
    assert out["result"]["evidence"][-1]["data"]["reviewer"] == "asha"
    row = runtime_module.get_runtime().reviews.get(run_id)
    assert row["reviewer"] == "asha"


# ------------------------------------------------------------------ fail closed
def test_with_no_keys_configured_everything_protected_is_503_not_open(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.delenv("AGENT_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)
    from fastapi.testclient import TestClient
    import api
    c = TestClient(api.app)
    assert c.post("/request", json={"request_id": "r", "raw_input": "hi"}).status_code == 503
    assert c.get("/reviews", headers=bearer(REVIEWER_KEY)).status_code == 503
    assert c.get("/health").status_code == 200


def test_unusable_entries_are_ignored_and_never_logged(monkeypatch, caplog):
    from auth import configured_keys
    good = "good-key-" + "g" * 20
    monkeypatch.setenv("API_KEYS", f"ok:client:{good},short:client:tooshort,bad:admin:{'z'*30},nonsense,dup:reviewer:{good}, :client:{'q'*30}")
    with caplog.at_level(logging.ERROR):
        keys = configured_keys()
    assert [(p.name, p.role) for _, p in keys] == [("ok", "client")]
    assert good not in caplog.text and "tooshort" not in caplog.text and "z" * 30 not in caplog.text


def test_explicit_dev_mode_is_open_and_needs_a_reviewer_name_in_the_body(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("AGENT_AUTH_DISABLED", "true")
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("backend_ticketing", 0.95))
    from fastapi.testclient import TestClient
    import api
    c = TestClient(api.app)
    run_id = c.post("/request", json={"request_id": "r", "raw_input": "phishing"}).json()["run_id"]
    assert c.post(f"/reviews/{run_id}/resume", json={"action": "reject"}).status_code == 422  # no name
    ok = c.post(f"/reviews/{run_id}/resume", json={"action": "reject", "reviewer": "dev"})
    assert ok.status_code == 200 and ok.json()["audit_trail"][-1]["reviewer"] == "dev"


def test_every_configured_key_is_compared_so_timing_does_not_reveal_which_matched(monkeypatch):
    from auth import authenticate
    monkeypatch.delenv("AGENT_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_KEYS", f"a:client:{CLIENT_KEY},b:reviewer:{REVIEWER_KEY}")
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    assert authenticate(f"Bearer {CLIENT_KEY}").name == "a"  # the first key matches...
    assert len(calls) == 2  # ...and both were still compared
