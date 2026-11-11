"""Routing: lazy clients, timeouts and retries, and what happens when classifiers fail."""
import dataclasses
import subprocess
import sys

import pytest

from helpers import invoke_graph

import orchestrator
from clients import get_typesafe_client, typesafe_client_kwargs
from contract import DepartmentResult
from graph import build_graph
from state import new_state


def test_importing_the_api_needs_no_keys_and_no_network():
    """The whole point of lazy clients, checked in a fresh interpreter with no key in the environment."""
    code = "import api, orchestrator, hr_complaints, clients; print('imported')"
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=".", timeout=60)
    assert out.returncode == 0 and "imported" in out.stdout, out.stderr[-800:]


def test_typesafe_client_is_created_on_first_use_and_complains_clearly_without_a_key():
    get_typesafe_client.cache_clear()
    with pytest.raises(Exception) as err:
        get_typesafe_client()
    assert "API key" in str(err.value)


def test_typesafe_client_has_a_timeout_and_bounded_retries(monkeypatch):
    monkeypatch.setenv("TYPESAFE_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("TYPESAFE_MAX_RETRIES", "4")
    kw = typesafe_client_kwargs()
    assert kw["timeout"] == 3.0 and kw["retry"].max_retries == 4 and kw["retry"].timeout == 25.0


def test_a_transient_server_error_is_retried_then_surfaces_as_an_exception():
    httpx2 = pytest.importorskip("httpx2")
    from typesafe_sdk import Choice, TypeSafeClient

    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(503, json={"error": "unavailable"})

    kw = typesafe_client_kwargs()
    kw["retry"] = dataclasses.replace(kw["retry"], backoff_initial=0.0, backoff_max=0.0)
    client = TypeSafeClient(api_key="k", transport=httpx2.MockTransport(handler), **kw)
    with pytest.raises(Exception):
        client.system_one(state="x", questions={"d": Choice(instructions="?", criteria={"a": "b", "c": "d"})})
    assert len(calls) == 3  # one attempt + two retries, then it gives up


def test_with_no_key_and_no_llm_budget_a_request_goes_to_a_human_not_a_crash():
    """Uses the real local_classify (no mocks): the missing key must be handled, not raised."""
    get_typesafe_client.cache_clear()
    final = invoke_graph(new_state("rt1", "pay the vendor invoice", llm_budget=0))
    result = DepartmentResult.from_state(final["department_result"])
    assert result.status == "needs_human" and result.reason == "classifier_unavailable"
    assert [e["node"] for e in final["audit_trail"]] == ["orchestrator", "queue_review"]


# ----------------------------------------------------------------- decision logic
def routing(monkeypatch, jev, llm=("none", None, 5)):
    monkeypatch.setattr(orchestrator, "local_classify", jev if callable(jev) else (lambda t: jev))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: llm if llm[0] != "none" else (None, llm[1] or "unsure", budget))
    return orchestrator.route("text", 5)


def test_confident_jev_answer_is_used_without_asking_the_llm(monkeypatch):
    monkeypatch.setattr(orchestrator, "llm_classify", lambda *a: pytest.fail("LLM must not be asked"))
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("finance", 0.80))
    r = orchestrator.route("text", 5)
    assert (r.department, r.source, r.reason) == ("finance", "local_model", None)


def test_low_confidence_jev_answer_falls_back_to_the_llm(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("finance", 0.60))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: ("legal_compliance", "ok", budget - 1))
    r = orchestrator.route("text", 5)
    assert (r.department, r.source, r.budget) == ("legal_compliance", "llm", 4)


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("ROUTER_MIN_CONFIDENCE", "0.5")
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("finance", 0.60))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda *a: pytest.fail("not needed"))
    assert orchestrator.route("t", 5).department == "finance"


def test_a_department_name_jev_invents_is_not_trusted(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("astrology", 0.99))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "unsure", budget - 1))
    r = orchestrator.route("t", 5)
    assert r.department is None and r.reason == "request_not_classified"


def test_both_unsure_is_not_classified_but_any_outage_is_unavailable(monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.2))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "unsure", budget - 1))
    assert orchestrator.route("t", 5).reason == "request_not_classified"
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "error", budget - 1))
    assert orchestrator.route("t", 5).reason == "classifier_unavailable"

    def boom(t): raise TimeoutError("slow")
    monkeypatch.setattr(orchestrator, "local_classify", boom)
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "no_budget", budget))
    assert orchestrator.route("t", 0).reason == "classifier_unavailable"


# ------------------------------------------------------------------- llm_classify
class FakeBedrock:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.exc:
            raise self.exc
        return type("R", (), {"content": [type("C", (), {"text": self.text})()]})()


def test_llm_classify_outcomes(monkeypatch):
    def with_client(client):
        monkeypatch.setattr(orchestrator, "get_bedrock_client", lambda: client)

    with_client(FakeBedrock(" Legal_Compliance \n"))
    assert orchestrator.llm_classify("t", 3) == ("legal_compliance", "ok", 2)
    with_client(FakeBedrock("human_escalation"))
    assert orchestrator.llm_classify("t", 3) == (None, "unsure", 2)
    with_client(FakeBedrock("a long reply about nothing"))
    assert orchestrator.llm_classify("t", 3) == (None, "unsure", 2)
    with_client(FakeBedrock(exc=RuntimeError("no credentials")))
    assert orchestrator.llm_classify("t", 3) == (None, "error", 2)  # a failed call still costs budget
    spent = FakeBedrock("finance")
    with_client(spent)
    assert orchestrator.llm_classify("t", 0) == (None, "no_budget", 0) and spent.calls == 0
