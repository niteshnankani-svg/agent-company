"""One place for how this project talks to the services departments depend on.

Every service address comes from the environment (see .env.example); nothing is hard-coded, and a
missing address is a clear ``not_configured`` result, not a crash. Every call has a timeout from the
environment. Errors are reduced to a short code plus something safe to show: an exception's message
and a remote service's response body are logged on the server but never passed on, because either
can contain internal hostnames, paths or another system's stack trace.
"""
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_transport: Optional[httpx.AsyncBaseTransport] = None  # tests install an httpx.MockTransport here


def set_transport(transport: Optional[httpx.AsyncBaseTransport]) -> None:
    global _transport
    _transport = transport


def service_url(env_name: str) -> Optional[str]:
    """The configured address, or None if it is unset or not an http(s) URL."""
    value = os.environ.get(env_name, "").strip()
    return value if value.startswith(("http://", "https://")) else None


def timeout_seconds(env_name: str, default: float) -> float:
    try:
        value = float(os.environ[env_name])
        return value if value > 0 else default
    except (KeyError, ValueError):
        return default


def http_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


def not_configured(env_name: str) -> dict:
    return {"error": "not_configured", "detail": f"{env_name} is not set to an http(s) URL"}


def error_from_exception(exc: BaseException, timeout: float, service: str = "service") -> dict:
    """Map an exception from a call to a safe, short error dict. Details go to the log only."""
    if isinstance(exc, httpx.TimeoutException):
        return {"error": "timeout", "detail": f"no response within {timeout:g}s"}
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        log.warning("%s returned HTTP %s: %s", service, code, exc.response.text[:300])
        return {"error": "http_status", "status_code": code, "detail": f"HTTP {code}"}
    if isinstance(exc, ValueError):  # includes JSON decoding errors
        log.warning("%s returned an unreadable reply", service)
        return {"error": "bad_response", "detail": "the reply was not valid JSON"}
    log.warning("%s call failed: %s", service, type(exc).__name__)
    return {"error": "unreachable" if isinstance(exc, httpx.HTTPError) else "request_failed", "detail": type(exc).__name__}


async def post_json(service: str, url_env: str, payload: dict, timeout_env: str, default_timeout: float) -> dict:
    """POST JSON to the service at ``$url_env`` and return its JSON object, or an ``{"error": ...}`` dict."""
    url = service_url(url_env)
    if url is None:
        return not_configured(url_env)
    timeout = timeout_seconds(timeout_env, default_timeout)
    try:
        async with http_client(timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return error_from_exception(exc, timeout, service)
    if not isinstance(data, dict):
        return {"error": "bad_response", "detail": "expected a JSON object"}
    return data
