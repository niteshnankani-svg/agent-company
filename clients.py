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


ROUTER_MODEL_ID = os.environ.get("ROUTER_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")


@lru_cache(maxsize=1)
def get_bedrock_client():
    from anthropic import AnthropicBedrock  # imported here so a missing package is a call-time error

    return AnthropicBedrock(
        aws_region=os.environ.get("BEDROCK_REGION", "us-east-1"),
        timeout=env_float("BEDROCK_TIMEOUT_SECONDS", 10.0),
        max_retries=env_int("BEDROCK_MAX_RETRIES", 1),
    )
