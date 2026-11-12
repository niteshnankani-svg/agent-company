"""Shared test helpers."""
import uuid

from langgraph.checkpoint.memory import InMemorySaver

import verifier
from graph import build_graph


def supports_everything(monkeypatch):
    """A checker that finds every claim supported, quoting the start of the evidence it was given."""
    calls = []

    def fake(question, answer, evidence):
        calls.append({"question": question, "answer": answer, "evidence": evidence})
        return {"claims": [{"claim": "the answer's claim", "verdict": "supported", "quote": evidence[:40]}]}

    monkeypatch.setattr(verifier, "_call_checker", fake)
    return calls


def invoke_graph(state):
    """Run a request through the whole graph with an in-memory checkpointer (a run that needs a person
    pauses at the human node and returns the state as it stood when it paused)."""
    return build_graph(InMemorySaver()).invoke(state, {"configurable": {"thread_id": uuid.uuid4().hex}})
