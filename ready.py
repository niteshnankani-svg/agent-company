"""Readiness: which departments can actually do their job right now.

``/health`` says the process is up. ``/ready`` says whether it can usefully take requests: the storage
works, authentication is set up, and each department's service answers. A department whose service is
down does not make the API unready, because the graph reports that department as ``failed`` and carries
on; it makes the API ``degraded`` so an operator sees it.

A probe is a short GET to the address (or to ``<NAME>_PROBE_URL`` if set). Any HTTP reply means the
server is reachable; a connection error or timeout means unreachable; a 5xx means unhealthy. Probes run
concurrently and the result is cached for a few seconds so this endpoint cannot be used to hammer the
services. Only status words and variable names are returned, never addresses.
"""
import asyncio
import os
import time
from typing import Optional

import httpx

from auth import auth_status
from clients import anthropic_key_configured, env_float
from services import http_client, service_url

# department -> the environment variables holding the addresses of the services it needs
DEPARTMENT_SERVICES: dict[str, list[str]] = {
    "finance": [],
    "hr_complaints": ["HR_AGENT_QUERY_URL"],
    "backend_ticketing": ["TICKETING_URL"],
    "export_compliance": ["NIRYAT_CHAT_URL", "NIRYAT_TRADEDATA_URL"],
    "legal_compliance": ["LEGAL_QUERY_URL"],
}

_cache: Optional[tuple[float, dict]] = None


async def probe(env_name: str) -> str:
    override = os.environ.get(f"{env_name}_PROBE_URL", "").strip()
    url = override if override.startswith(("http://", "https://")) else service_url(env_name)
    if url is None:
        return "not_configured"
    try:
        async with http_client(env_float("READY_PROBE_TIMEOUT_SECONDS", 3.0)) as client:
            response = await client.get(url, follow_redirects=False)
    except httpx.HTTPError:
        return "unreachable"
    except Exception:
        return "unreachable"
    return "unhealthy" if response.status_code >= 500 else "reachable"


def _department_status(statuses: list[str]) -> str:
    if not statuses:
        return "ok"  # no external service (Finance)
    for bad in ("not_configured", "unreachable", "unhealthy"):
        if bad in statuses:
            return bad
    return "ok"


async def check_storage(runtime_getter) -> str:
    async def touch() -> None:
        runtime = await asyncio.to_thread(runtime_getter)
        await asyncio.to_thread(runtime.reviews.count_pending)

    try:
        await asyncio.wait_for(touch(), timeout=env_float("READY_PROBE_TIMEOUT_SECONDS", 3.0) + 2.0)
        return "ok"
    except Exception:  # includes the timeout: a database that does not answer is not ready
        return "unavailable"


async def readiness(runtime_getter) -> tuple[dict, int]:
    """(report, http_status). Cached for READY_CACHE_SECONDS (default 5)."""
    global _cache
    ttl = env_float("READY_CACHE_SECONDS", 5.0)
    now = time.monotonic()
    if _cache and now - _cache[0] < ttl:
        return _cache[1], 200 if _cache[1]["status"] != "not_ready" else 503

    envs = sorted({e for envs in DEPARTMENT_SERVICES.values() for e in envs})
    results = await asyncio.gather(check_storage(runtime_getter), *(probe(e) for e in envs))
    storage, by_env = results[0], dict(zip(envs, results[1:]))

    departments = {
        name: {"status": _department_status([by_env[e] for e in names]), "services": {e: by_env[e] for e in names}}
        for name, names in DEPARTMENT_SERVICES.items()
    }
    dependencies = {
        "checkpoint_db": storage,
        "auth": auth_status(),
        # Not probed: these only say whether the setting is present.
        "router_key": "configured" if os.environ.get("TYPESAFE_API_KEY") else "not_configured",
        "anthropic_key": "configured" if anthropic_key_configured() else "not_configured",
        "answer_cache": "configured" if os.environ.get("EMBEDDING_PROVIDER") else "off",
    }
    if storage != "ok" or dependencies["auth"] == "not_configured":
        status = "not_ready"
    elif any(d["status"] != "ok" for d in departments.values()) or "not_configured" in (dependencies["router_key"], dependencies["anthropic_key"]):
        status = "degraded"
    else:
        status = "ready"
    report = {"status": status, "departments": departments, "dependencies": dependencies}
    _cache = (now, report)
    return report, 503 if status == "not_ready" else 200


def reset_cache() -> None:
    global _cache
    _cache = None
