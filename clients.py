"""Clients for the routing classifiers, created on first use, never at import.

Importing this project must not need a key or a network. A missing key therefore
surfaces when a request is classified (and is handled there as "classifier
unavailable"), not as a crash at startup or while collecting tests.

Every call has a timeout and a bounded retry. Defaults can be changed per
deployment through the environment variables named below.
"""
import os
from functools import lru_cache

from typesafe_sdk import RetryPolicy, TypeSafeClient


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def typesafe_client_kwargs() -> dict:
    """Per-request timeout, and retries with backoff for connection errors, timeouts and 408/429/5xx."""
    return {
        "timeout": env_float("TYPESAFE_TIMEOUT_SECONDS", 10.0),
        "retry": RetryPolicy(
            max_retries=env_int("TYPESAFE_MAX_RETRIES", 2),
            timeout=env_float("TYPESAFE_RETRY_BUDGET_SECONDS", 25.0),  # total time across retries
        ),
    }


@lru_cache(maxsize=1)
def get_typesafe_client() -> TypeSafeClient:
    # Reads TYPESAFE_API_KEY; raises TypeSafeError if it is not set. lru_cache does not
    # cache an exception, so setting the key later works without a restart.
    return TypeSafeClient(**typesafe_client_kwargs())


# Haiku 4.5 on Anthropic's own API. This model accepts temperature 0 and a forced tool call, which the
# extraction and verifier calls rely on. Newer models reject one or both: check before changing it.
ROUTER_MODEL_ID = os.environ.get("ROUTER_MODEL_ID", "claude-haiku-4-5")


def anthropic_key_configured() -> bool:
    """Whether a credential is present. Presence only: nothing is sent to Anthropic to check it."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@lru_cache(maxsize=1)
def get_anthropic_client():
    """Anthropic's API, used for the router fallback, Finance extraction and the verifier.

    The SDK reads ANTHROPIC_API_KEY itself. Without one, the first call raises, which every call site
    already treats as "model unavailable". lru_cache does not cache an exception, so setting the key
    later works without a restart.
    """
    from anthropic import Anthropic  # imported here so a missing package is a call-time error

    return Anthropic(
        timeout=env_float("ANTHROPIC_TIMEOUT_SECONDS", 10.0),
        max_retries=env_int("ANTHROPIC_MAX_RETRIES", 1),
    )
