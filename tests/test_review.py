"""Human-in-the-loop: pause at the human node, list pending reviews, resume with a decision.

The end-to-end tests run against a real SQLite file, and against a real Postgres too when
TEST_POSTGRES_URL is set. "Restart" means closing the runtime and building a new one from the same
database, which is what a process restart does.
"""
import concurrent.futures
import logging
import os
import threading
import uuid

import pytest
from pydantic import ValidationError

import legal_compliance
import orchestrator
import runtime as runtime_module
import verifier
from contract import DepartmentResult, Evidence
from helpers import supports_everything
from review import HumanDecision, apply_decision, has_draft, review_payload
from review_store import ReviewStore

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


def held(answer=None, **kw):
    base = dict(department="legal_compliance", status="needs_human", reason="verification_failed",
                evidence=[Evidence(source="legal_rag:BNS Section 357", text="t")])
    return DepartmentResult(**{**base, "answer": answer, **kw})


# ------------------------------------------------------------ decisions
def test_decision_validation():
    with pytest.raises(ValidationError):
        HumanDecision(action="answer", reviewer="r")  # answer needs text
    with pytest.raises(ValidationError):
        HumanDecision(action="approve", reviewer="r", answer="text")  # text only with 'answer'
    with pytest.raises(ValidationError):
        HumanDecision(action="approve", reviewer="")
    with pytest.raises(ValidationError):
        HumanDecision(action="delete", reviewer="r")
    with pytest.raises(ValidationError):
        HumanDecision(action="approve", reviewer="r", surprise=1)


def test_approve_releases_the_draft_and_records_who_approved():
    final, verification = apply_decision(held("The draft."), HumanDecision(action="approve", reviewer="asha", note="checked the statute"))
    assert final.status == "answered" and final.answer == "The draft."
    assert final.evidence[0].source == "legal_rag:BNS Section 357"  # the original evidence is kept
    assert final.evidence[-1].source == "human_review"
    assert final.evidence[-1].data == {"decision": "approve", "reviewer": "asha", "note": "checked the statute"}
    assert verification == {"status": "human_approved", "check": "human_review", "reviewer": "asha"}


def test_approve_without_a_draft_is_impossible():
    with pytest.raises(ValueError):
        apply_decision(held(None), HumanDecision(action="approve", reviewer="asha"))


def test_a_human_written_answer_replaces_the_draft_and_does_not_inherit_its_evidence():
    final, verification = apply_decision(held("Wrong draft."), HumanDecision(action="answer", reviewer="asha", answer="The right answer."))
    assert final.status == "answered" and final.answer == "The right answer."
    assert [e.source for e in final.evidence] == ["human_review"]
    assert verification["status"] == "human_provided"


def test_reject_declines_with_the_note_and_is_not_an_answer():
    final, verification = apply_decision(held("Draft."), HumanDecision(action="reject", reviewer="asha", note="out of scope"))
    assert final.status == "failed" and final.reason == "declined_by_reviewer" and final.error == "Declined by a reviewer: out of scope"
    assert final.answer is None and verification["status"] == "not_applicable"
    assert apply_decision(held(), HumanDecision(action="reject", reviewer="a"))[0].error == "Declined by a reviewer: no reason given"


def test_payload_offers_approve_only_when_there_is_a_draft():
    assert review_payload("r", held("d"), "q")["allowed_actions"] == ["approve", "answer", "reject"]
    assert review_payload("r", held(None), "q")["allowed_actions"] == ["answer", "reject"]
    assert has_draft(held("d")) and not has_draft(held("  "))


# ----------------------------------------------------------------- the inbox
@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        s = ReviewStore.sqlite(str(tmp_path / "reviews.sqlite"))
        yield s
        s.close()
        return
    if not POSTGRES_URL:
        pytest.skip("TEST_POSTGRES_URL not set")
    from psycopg_pool import ConnectionPool
    pool = ConnectionPool(POSTGRES_URL, max_size=10, kwargs={"autocommit": True}, open=True)
    s = ReviewStore.postgres(pool)
    s._db.run("DELETE FROM reviews")
    yield s
    pool.close()


def add(store, run_id, **kw):
    args = dict(request_id="req", department="finance", reason="r", question="q", has_draft=False)
    args.update(kw)
    store.add(run_id, **args)


def test_inbox_lists_pending_oldest_first_and_counts(store):
    ids = [uuid.uuid4().hex for _ in range(3)]
    for i in ids:
        add(store, i)
    assert [r["run_id"] for r in store.list_pending()] == ids
    assert store.count_pending() == 3
    assert [r["run_id"] for r in store.list_pending(limit=2, offset=1)] == ids[1:]
    row = store.get(ids[0])
    assert row["status"] == "pending" and row["has_draft"] is False and row["department"] == "finance"


def test_adding_the_same_run_twice_is_harmless(store):
    rid = uuid.uuid4().hex
    add(store, rid, reason="first")
    add(store, rid, reason="second")
    assert store.count_pending() == 1 and store.get(rid)["reason"] == "first"


def test_claim_release_resolve_lifecycle(store):
    rid = uuid.uuid4().hex
    add(store, rid)
    assert store.claim(rid) is True and store.claim(rid) is False  # taken
    assert store.list_pending() == [] and store.get(rid)["status"] == "resuming"
    store.release(rid)
    assert store.claim(rid) is True
    store.resolve(rid, "approve", "asha")
    row = store.get(rid)
    assert row["status"] == "resolved" and row["action"] == "approve" and row["reviewer"] == "asha" and row["resolved_at"]
    assert store.claim(rid) is False and store.get("nope") is None


def test_exactly_one_of_many_simultaneous_claims_wins(store):
    rid = uuid.uuid4().hex
    add(store, rid)
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        wins = list(pool.map(lambda _: store.claim(rid), range(16)))
    assert wins.count(True) == 1


def test_long_questions_are_bounded(store):
    rid = uuid.uuid4().hex
    add(store, rid, question="x" * 10000)
    assert len(store.get(rid)["question"]) == 2000


# ------------------------------------------------------------------- runtime
def test_runtime_uses_sqlite_by_default_and_postgres_when_a_url_is_set(monkeypatch, tmp_path, caplog):
    runtime_module.reset_runtime()
    rt = runtime_module.get_runtime()
    assert rt.backend == "sqlite" and (tmp_path / "checkpoints.sqlite").exists()
    runtime_module.reset_runtime()

    seen = []
    monkeypatch.setattr(runtime_module, "_postgres_runtime", lambda url: seen.append(url) or "PG")
    monkeypatch.setenv("CHECKPOINT_DB_URL", "postgresql://user:s3cret@db.example:5432/agent")
    with caplog.at_level(logging.INFO):
        assert runtime_module.build_runtime() == "PG"
    assert seen == ["postgresql://user:s3cret@db.example:5432/agent"]
    assert "s3cret" not in caplog.text, "the database URL carries credentials and must never be logged"


# ------------------------------------------------------- end to end through the API
@pytest.fixture(params=["sqlite", "postgres"])
def client(request, monkeypatch):
    pytest.importorskip("fastapi")
    if request.param == "postgres":
        if not POSTGRES_URL:
            pytest.skip("TEST_POSTGRES_URL not set")
        monkeypatch.setenv("CHECKPOINT_DB_URL", POSTGRES_URL)
    runtime_module.reset_runtime()
    if request.param == "postgres":
        runtime_module.get_runtime().reviews._db.run("DELETE FROM reviews")
    from fastapi.testclient import TestClient
    import api
    yield TestClient(api.app)
    runtime_module.reset_runtime()


def route_to(monkeypatch, dept):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (dept, 0.95))


def ask(client, text="this looks like phishing", rid="r1"):
    return client.post("/request", json={"request_id": rid, "raw_input": text}).json()


def decide(client, run_id, **body):
    return client.post(f"/reviews/{run_id}/resume", json={"reviewer": "asha", **body})


def pending_ids(client):
    return [r["run_id"] for r in client.get("/reviews", params={"limit": 200}).json()["reviews"]]


def test_a_request_that_needs_a_person_pauses_and_appears_in_the_inbox(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    body = ask(client)
    assert body["result"]["status"] == "needs_human" and body["result"]["reason"] == "security_incident_review"
    assert body["pending_review"] is True and body["needs_human"] is True and body["run_id"]
    listing = client.get("/reviews").json()
    mine = [r for r in listing["reviews"] if r["run_id"] == body["run_id"]][0]
    assert mine["department"] == "backend_ticketing" and mine["reason"] == "security_incident_review"
    assert mine["has_draft"] is False and mine["question"] == "this looks like phishing" and listing["count"] >= 1
    run = client.get(f"/runs/{body['run_id']}").json()
    assert run["pending_review"] is True and [e["node"] for e in run["audit_trail"]][-1] == "queue_review"


def test_reject_resumes_the_run_and_records_who_decided(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    out = decide(client, run_id, action="reject", note="not an incident")
    assert out.status_code == 200
    body = out.json()
    assert body["pending_review"] is False and body["needs_human"] is False
    assert body["result"]["status"] == "failed" and body["result"]["reason"] == "declined_by_reviewer"
    assert body["verification"]["check"] == "human_review"
    last = body["audit_trail"][-1]
    assert last["node"] == "human_review" and last["decision"] == "reject" and last["reviewer"] == "asha"
    assert run_id not in pending_ids(client)
    assert client.get(f"/runs/{run_id}").json()["pending_review"] is False


def test_approve_is_refused_when_there_is_no_draft(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    assert decide(client, run_id, action="approve").status_code == 422
    assert run_id in pending_ids(client)  # still waiting


def test_a_human_can_write_the_answer(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    body = decide(client, run_id, action="answer", answer="This is a known false positive.").json()
    assert body["result"]["status"] == "answered" and body["result"]["answer"] == "This is a known false positive."
    assert body["verification"]["status"] == "human_provided"


CIT = {"act": "BNS", "section_number": "357", "title": "Breach of contract", "summary": "S", "score": 0.64}


def withdrawn_legal(monkeypatch):
    """A Legal answer the verifier rejects twice, so it is withdrawn to a person as a draft."""
    async def fake(question, act_filter): return {"answer": "The fine is five thousand rupees.", "citations": [CIT]}
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", fake)
    monkeypatch.setattr(verifier, "_call_checker", lambda q, a, e: {"claims": [{"claim": "the fine is 5000", "verdict": "not_in_evidence"}]})


def test_approving_a_withdrawn_draft_releases_it_labelled_as_human_approved_and_does_not_cache_it(client, monkeypatch):
    withdrawn_legal(monkeypatch)
    first = ask(client, "What is the fine under Section 357 of BNS?")
    assert first["result"]["status"] == "needs_human" and first["result"]["reason"] == "verification_failed"
    assert first["result"]["answer"] == "The fine is five thousand rupees." and first["pending_review"] is True
    listed = [r for r in client.get("/reviews").json()["reviews"] if r["run_id"] == first["run_id"]][0]
    assert listed["has_draft"] is True
    body = decide(client, first["run_id"], action="approve", note="matches the schedule").json()
    assert body["result"]["status"] == "answered" and body["result"]["answer"] == "The fine is five thousand rupees."
    sources = [e["source"] for e in body["result"]["evidence"]]
    assert sources == ["legal_rag:BNS Section 357", "verifier", "human_review"]  # the findings stay visible
    assert body["verification"] == {"status": "human_approved", "check": "human_review", "reviewer": "asha"}


def test_a_router_failure_also_waits_for_a_person(client, monkeypatch):
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: (None, 0.0))
    monkeypatch.setattr(orchestrator, "llm_classify", lambda text, budget: (None, "unsure", budget - 1))
    body = ask(client, "asdkjfh help")
    assert body["pending_review"] is True and body["result"]["reason"] == "request_not_classified"
    assert decide(client, body["run_id"], action="answer", answer="Please contact IT.").json()["result"]["status"] == "answered"


def test_a_decision_cannot_be_applied_twice(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    assert decide(client, run_id, action="reject").status_code == 200
    again = decide(client, run_id, action="answer", answer="changed my mind")
    assert again.status_code == 409
    assert client.get(f"/runs/{run_id}").json()["result"]["status"] == "failed"  # the first decision stands


def test_a_paused_run_survives_a_restart_and_can_still_be_resumed(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    runtime_module.reset_runtime()  # the process "restarts": every connection and the compiled graph are gone
    assert run_id in pending_ids(client)
    run = client.get(f"/runs/{run_id}").json()
    assert run["pending_review"] is True and run["result"]["reason"] == "security_incident_review"
    body = decide(client, run_id, action="reject", note="after restart").json()
    assert body["result"]["status"] == "failed" and body["result"]["error"].endswith("after restart")


def test_of_two_simultaneous_resumes_exactly_one_is_applied(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    barrier = threading.Barrier(2)

    def go(i):
        barrier.wait()
        return decide(client, run_id, action="answer", answer=f"answer {i}").status_code

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(go, [1, 2]))
    assert codes == [200, 409]
    final = client.get(f"/runs/{run_id}").json()["result"]["answer"]
    assert final in ("answer 1", "answer 2")


def test_a_failed_resume_puts_the_review_back_so_it_can_be_retried(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    graph = runtime_module.get_runtime().graph
    real_invoke = graph.invoke
    monkeypatch.setattr(graph, "invoke", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database hiccup")), raising=False)
    failed = decide(client, run_id, action="reject")
    assert failed.status_code == 500 and "hiccup" not in failed.text  # no internals in the response
    assert run_id in pending_ids(client)
    monkeypatch.setattr(graph, "invoke", real_invoke, raising=False)
    assert decide(client, run_id, action="reject").status_code == 200


def test_unknown_runs_are_404(client):
    assert client.get("/runs/nope").status_code == 404
    assert decide(client, "nope", action="reject").status_code == 404


def test_resuming_a_run_that_never_paused_is_409(client, monkeypatch):
    supports_everything(monkeypatch)
    async def fake(question, act_filter): return {"answer": "A", "citations": [CIT]}
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", fake)
    body = ask(client, "What is Section 357 of BNS about?")
    assert body["result"]["status"] == "answered" and body["pending_review"] is False
    assert body["run_id"] not in pending_ids(client)
    assert decide(client, body["run_id"], action="reject").status_code == 409


def test_runs_that_do_not_need_a_person_do_not_pause(client, monkeypatch):
    route_to(monkeypatch, "finance")  # no model in tests -> the finance form, status needs_more_info
    body = ask(client, "please pay the vendor")
    assert body["result"]["status"] == "needs_more_info" and body["pending_review"] is False
    assert body["run_id"] not in pending_ids(client)


def test_every_run_gets_its_own_server_generated_id_even_if_the_client_reuses_a_request_id(client, monkeypatch):
    route_to(monkeypatch, "backend_ticketing")
    a, b = ask(client, "phishing one", rid="same"), ask(client, "phishing two", rid="same")
    assert a["run_id"] != b["run_id"] and a["request_id"] == b["request_id"] == "same"
    assert len(a["audit_trail"]) == len(b["audit_trail"])  # b did not inherit a's trail
    assert decide(client, a["run_id"], action="reject").status_code == 200
    assert client.get(f"/runs/{b['run_id']}").json()["pending_review"] is True  # untouched


@pytest.mark.parametrize("body", [
    {"action": "answer"}, {"action": "approve", "answer": "x"}, {"action": "maybe"}, {"action": "reject", "extra": 1},
    {"action": "reject", "reviewer": ""},
])
def test_malformed_decisions_are_rejected_before_touching_the_run(client, monkeypatch, body):
    route_to(monkeypatch, "backend_ticketing")
    run_id = ask(client)["run_id"]
    payload = {"reviewer": "asha", **body}
    assert client.post(f"/reviews/{run_id}/resume", json=payload).status_code == 422
    assert run_id in pending_ids(client)
