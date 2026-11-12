"""The semantic cache: what it may and may never store, and that it cannot confuse near-miss questions."""
import hashlib
import math

import pytest

import cache as cache_module
import legal_compliance
import orchestrator
from cache import (
    LocalEmbedder, SemanticCache, discriminators, embedder_from_env, why_not_cacheable,
)
from contract import DepartmentResult, Evidence
from graph import build_graph
from helpers import supports_everything, invoke_graph
from state import new_state


class WordEmbedder:
    """Deterministic stand-in: hashes words into a vector, so paraphrases score high and
    one-word differences ('357' vs '358') ALSO score high, like a real sentence embedding."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def embed(self, text):
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding service down")
        v = [0.0] * 4096
        for w in "".join(c.lower() if c.isalnum() else " " for c in text).split():
            # md5, not hash(): Python's hash() is randomised per process, which made this test flaky
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 4096] += 1.0
        return v


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def answered(text="Answer."):
    return DepartmentResult(department="legal_compliance", status="answered", answer=text,
                            evidence=[Evidence(source="legal_rag:BNS Section 357", text="t")])


def make(**kw):
    kw.setdefault("embedder", WordEmbedder())
    return SemanticCache(**kw)


Q = "What is the punishment for murder under BNS?"


# ---------------------------------------------------------------------- policy
@pytest.mark.parametrize("dept", ["finance", "hr_complaints", "backend_ticketing", "export_compliance", "mystery"])
def test_non_static_departments_are_never_cacheable(dept):
    assert why_not_cacheable(dept, Q) is not None


@pytest.mark.parametrize("text,fragment", [
    ("Email me the answer at bob@corp.com about Section 357 of BNS", "personal identifiers"),
    ("Call 555-123-4567 about Section 357", "personal identifiers"),
    ("Can my landlord evict me under BNS?", "specific person"),
    ("What are our obligations under DPDP?", "specific person"),
])
def test_requests_about_a_person_are_not_cacheable_even_for_legal(text, fragment):
    assert fragment in why_not_cacheable("legal_compliance", text)


def test_only_answered_results_are_cacheable():
    for status, kw in [("needs_human", dict(reason="x")), ("failed", dict(error="e")), ("needs_more_info", dict(missing_fields=["a"]))]:
        r = DepartmentResult(department="legal_compliance", status=status, **kw)
        assert "never cached" in why_not_cacheable("legal_compliance", Q, r)
    assert why_not_cacheable("legal_compliance", Q, answered()) is None


# ---------------------------------------------------------------- discriminators
def test_discriminators_capture_numbers_acronyms_and_names():
    assert discriminators("What is Section 357 of BNS about?") == frozenset({"357", "BNS", "section"})
    assert discriminators("How many employees are in the Sales department?") == frozenset({"sales"})
    assert discriminators("what is the punishment for murder") == frozenset()


# ------------------------------------------------------------------ cache behaviour
def test_identical_and_paraphrased_questions_hit():
    c = make()
    assert c.store("legal_compliance", Q, answered()) is None
    assert c.lookup("legal_compliance", Q).result["answer"] == "Answer."
    assert c.lookup("legal_compliance", "What is the punishment for murder under BNS") is not None  # punctuation only


def test_the_357_vs_358_bug_cannot_happen_even_though_the_embeddings_are_nearly_identical():
    c = make(threshold=0.5)  # even a very permissive threshold must not let it through
    c.store("legal_compliance", "What is Section 357 of BNS about?", answered("357"))
    assert c.embedder.embed("What is Section 357 of BNS about?") != c.embedder.embed("What is Section 358 of BNS about?")
    assert c.lookup("legal_compliance", "What is Section 358 of BNS about?") is None
    assert c.lookup("legal_compliance", "What is Section 357 of BNSS about?") is None  # BNS vs BNSS
    assert c.lookup("legal_compliance", "What is Section 357 of BNS about?") is not None


def test_the_guard_is_what_prevents_the_collision_not_luck(monkeypatch):
    """Mutation check: switch the guard off and the old bug comes straight back."""
    c = make(threshold=0.5)
    c.store("legal_compliance", "What is Section 357 of BNS about?", answered("357"))
    a = c.embedder.embed("What is Section 357 of BNS about?"); b = c.embedder.embed("What is Section 358 of BNS about?")
    cos = sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    assert cos > 0.8, "the two questions look almost identical to an embedding"
    monkeypatch.setattr(cache_module, "discriminators", lambda text: frozenset())
    c2 = make(threshold=0.5)
    c2.store("legal_compliance", "What is Section 357 of BNS about?", answered("357"))
    assert c2.lookup("legal_compliance", "What is Section 358 of BNS about?") is not None  # the bug, reproduced


def test_a_different_scope_or_department_never_hits():
    c = make()
    c.store("legal_compliance", Q, answered(), scope="BNS")
    assert c.lookup("legal_compliance", Q, scope="BNSS") is None
    assert c.lookup("legal_compliance", Q, scope="") is None
    assert c.lookup("legal_compliance", Q, scope="BNS") is not None


def test_below_threshold_is_a_miss():
    stored, asked = "what is the punishment for murder under BNS", "punishment for murder BNS what is the typical sentence and fine"
    strict, lax = make(threshold=0.99), make(threshold=0.5)
    for c in (strict, lax):
        c.store("legal_compliance", stored, answered())
    assert lax.lookup("legal_compliance", asked) is not None
    assert strict.lookup("legal_compliance", asked) is None


def test_entries_expire_after_the_ttl():
    clock = Clock()
    c = make(ttl_seconds=60, clock=clock)
    c.store("legal_compliance", Q, answered())
    clock.t += 59
    assert c.lookup("legal_compliance", Q) is not None
    clock.t += 2
    assert c.lookup("legal_compliance", Q) is None and len(c) == 0


def test_least_recently_used_entry_is_evicted_at_capacity():
    c = make(max_entries=2)
    qs = ["murder under BNS", "theft under BNS", "fraud under BNS"]
    c.store("legal_compliance", qs[0], answered("a")); c.store("legal_compliance", qs[1], answered("b"))
    assert c.lookup("legal_compliance", qs[0]) is not None  # touch the first one
    c.store("legal_compliance", qs[2], answered("c"))
    assert c.lookup("legal_compliance", qs[1]) is None  # the untouched one was evicted
    assert c.lookup("legal_compliance", qs[0]) is not None and len(c) == 2


def test_store_refuses_what_policy_forbids():
    c = make()
    assert c.store("hr_complaints", "How many employees?", answered()) is not None
    assert c.store("legal_compliance", "my case under BNS", answered()) is not None
    assert c.store("legal_compliance", Q, DepartmentResult(department="legal_compliance", status="failed", error="e")) is not None
    assert len(c) == 0


def test_embedder_failure_is_a_miss_and_a_skipped_store_never_an_error():
    c = make(embedder=WordEmbedder(fail=True))
    assert c.lookup("legal_compliance", Q) is None
    assert c.store("legal_compliance", Q, answered()) == "embedder unavailable"


@pytest.mark.parametrize("bad", [[], [0.0, 0.0], [math.nan, 1.0]])
def test_unusable_embeddings_are_rejected(bad):
    class Bad:
        def embed(self, t): return bad
    assert make(embedder=Bad()).store("legal_compliance", Q, answered()) == "embedder unavailable"


def test_policy_is_checked_before_the_embedder_is_called():
    emb = WordEmbedder()
    c = make(embedder=emb)
    c.lookup("hr_complaints", "How many employees are in Sales?")
    c.lookup("legal_compliance", "my own question about BNS")
    assert emb.calls == 0  # nothing that may not be cached is ever sent to the embedding service


# ------------------------------------------------------------------------ embedders
def test_embedder_from_env(monkeypatch):
    assert embedder_from_env()[0] is None  # nothing configured -> cache off
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    assert isinstance(embedder_from_env()[0], LocalEmbedder)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bedrock")  # removed with AWS: now an unknown provider, cache stays off
    emb, note = embedder_from_env()
    assert emb is None and "bedrock" in note
    monkeypatch.setenv("EMBEDDING_PROVIDER", "typo")
    emb, note = embedder_from_env()
    assert emb is None and "typo" in note


def test_get_cache_is_off_without_a_provider_and_built_from_env_with_one(monkeypatch):
    cache_module._cache_ready = False
    assert cache_module.get_cache()[0] is None
    cache_module._cache_ready = False
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "123")
    monkeypatch.setenv("CACHE_SIMILARITY_THRESHOLD", "0.97")
    built, _ = cache_module.get_cache()
    assert built.ttl == 123.0 and built.threshold == 0.97


# ------------------------------------------------------------------ in the graph
CIT = {"act": "BNS", "section_number": "357", "title": "T", "summary": "S", "score": 0.64}


def legal_graph_run(monkeypatch, text, calls, request_id="g"):
    supports_everything(monkeypatch)

    async def fake(question, act_filter):
        calls.append(question)
        return {"answer": f"Answer to {question}", "citations": [CIT]}
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))
    monkeypatch.setattr(legal_compliance, "ask_legal_question", fake)
    return invoke_graph(new_state(request_id, text))


def nodes(final): return [(e["node"], e["status"]) for e in final["audit_trail"]]


def test_graph_stores_after_verification_then_serves_the_second_ask_from_cache(monkeypatch):
    cache_module.set_cache(make())
    calls = []
    first = legal_graph_run(monkeypatch, Q, calls)
    assert ("cache_lookup", "miss") in nodes(first) and ("cache_store", "stored") in nodes(first)
    second = legal_graph_run(monkeypatch, Q, calls)
    assert len(calls) == 1, "the second identical question must not reach the Legal service"
    assert nodes(second) == [("orchestrator", "routed"), ("cache_lookup", "hit")]
    assert second["department_result"] == first["department_result"] and second["cache_hit"] is True


def test_graph_does_not_confuse_near_miss_questions(monkeypatch):
    cache_module.set_cache(make(threshold=0.5))
    calls = []
    legal_graph_run(monkeypatch, "What is Section 357 of BNS about?", calls)
    final = legal_graph_run(monkeypatch, "What is Section 358 of BNS about?", calls)
    assert len(calls) == 2 and ("cache_lookup", "miss") in nodes(final)


def test_graph_never_caches_or_looks_up_hr_finance_backend_export(monkeypatch):
    emb = WordEmbedder()
    cache_module.set_cache(make(embedder=emb))
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("finance", 0.9))
    final = invoke_graph(new_state("f1", "pay the vendor invoice"))
    statuses = dict(nodes(final))
    assert statuses["cache_lookup"] == "skipped" and statuses["cache_store"] == "not_cached"
    assert emb.calls == 0 and len(cache_module.get_cache()[0]) == 0


def test_graph_with_the_cache_off_just_works(monkeypatch):
    calls = []
    final = legal_graph_run(monkeypatch, Q, calls)
    assert dict(nodes(final))["cache_lookup"] == "disabled" and len(calls) == 1


def test_graph_does_not_cache_a_failed_or_escalated_answer(monkeypatch):
    cache_module.set_cache(make())
    monkeypatch.setattr(orchestrator, "local_classify", lambda t: ("legal_compliance", 0.9))

    async def down(*a, **k): return {"error": "timeout", "detail": "60s"}
    monkeypatch.setattr(legal_compliance, "ask_legal_question", down)
    final = invoke_graph(new_state("c1", Q))
    assert dict(nodes(final))["cache_store"] == "not_cached" and len(cache_module.get_cache()[0]) == 0


def test_graph_does_not_cache_sequential_runs(monkeypatch):
    cache_module.set_cache(make())
    supports_everything(monkeypatch)
    async def fake(*a, **k): return {"answer": "A", "citations": [CIT]}
    monkeypatch.setattr(legal_compliance, "ask_legal_question", fake)
    final = invoke_graph(new_state("s1", Q, department_sequence=["legal_compliance"]))
    assert dict(nodes(final))["cache_store"] == "skipped" and len(cache_module.get_cache()[0]) == 0


def test_graph_does_not_cache_a_question_about_a_person(monkeypatch):
    cache_module.set_cache(make())
    calls = []
    final = legal_graph_run(monkeypatch, "Am I liable under Section 357 of BNS?", calls)
    assert dict(nodes(final))["cache_lookup"] == "skipped" and len(cache_module.get_cache()[0]) == 0
