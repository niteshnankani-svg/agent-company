import pytest

import cache as cache_module


@pytest.fixture(autouse=True)
def isolate_process_wide_state(monkeypatch):
    """No test may depend on, or leak, real keys, a real embedder, or a populated cache."""
    for name in ("TYPESAFE_API_KEY", "EMBEDDING_PROVIDER", "ROUTER_MIN_CONFIDENCE"):
        monkeypatch.delenv(name, raising=False)
    cache_module.set_cache(None, "disabled for tests")  # individual tests install their own
    yield
    cache_module.set_cache(None, "disabled for tests")
