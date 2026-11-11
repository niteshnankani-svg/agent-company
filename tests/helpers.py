"""Shared test helpers."""
import verifier


def supports_everything(monkeypatch):
    """A checker that finds every claim supported, quoting the start of the evidence it was given."""
    calls = []

    def fake(question, answer, evidence):
        calls.append({"question": question, "answer": answer, "evidence": evidence})
        return {"claims": [{"claim": "the answer's claim", "verdict": "supported", "quote": evidence[:40]}]}

    monkeypatch.setattr(verifier, "_call_checker", fake)
    return calls
