import pytest

import cache as cache_module
import extraction
import verifier


@pytest.fixture(autouse=True)
def isolate_process_wide_state(monkeypatch):
    """No test may depend on, or leak, real keys, a real embedder, or a populated cache."""
    for name in ("TYPESAFE_API_KEY", "EMBEDDING_PROVIDER", "ROUTER_MIN_CONFIDENCE"):
        monkeypatch.delenv(name, raising=False)
    cache_module.set_cache(None, "disabled for tests")  # individual tests install their own

    def no_model(text, defs):
        raise RuntimeError("no extraction model in tests; a test that needs one installs a fake")

    def no_checker(question, answer, evidence):
        raise RuntimeError("no verifier model in tests; a test that needs one installs a fake")

    monkeypatch.setattr(extraction, "_call_model", no_model)
    monkeypatch.setattr(verifier, "_call_checker", no_checker)
    monkeypatch.delenv("VERIFIER_ON_UNAVAILABLE", raising=False)
    yield
    cache_module.set_cache(None, "disabled for tests")
