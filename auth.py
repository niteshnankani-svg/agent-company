"""API-key authentication with two roles, and the reviewer's identity.

``API_KEYS`` is a comma-separated list of ``name:role:key``:

* ``client``   may submit requests and look up a run;
* ``reviewer`` may also list pending reviews and decide them. The key's ``name`` is the reviewer
  recorded on every decision, so it cannot be self-declared.

Fail closed: with no valid keys configured, every protected endpoint answers 503 rather than letting
everyone in. ``AGENT_AUTH_DISABLED=true`` switches authentication off explicitly (local development only;
the caller is then anonymous and a reviewer name must be sent in the body). Keys are compared in constant
time, must be at least 24 characters, and are never logged.
"""
import hmac
import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

log = logging.getLogger(__name__)

ROLES = ("client", "reviewer")
MIN_KEY_CHARS = 24


@dataclass(frozen=True)
class Principal:
    name: str
    role: str


ANONYMOUS = Principal("anonymous", "reviewer")


def auth_disabled() -> bool:
    return os.environ.get("AGENT_AUTH_DISABLED", "").strip().lower() == "true"


def configured_keys() -> list[tuple[str, Principal]]:
    keys: list[tuple[str, Principal]] = []
    seen: set[str] = set()
    for entry in filter(None, (e.strip() for e in os.environ.get("API_KEYS", "").split(","))):
        parts = entry.split(":", 2)
        if len(parts) != 3:
            log.error("API_KEYS: an entry is not name:role:key and was ignored")
            continue
        name, role, key = (p.strip() for p in parts)
        if not name or role not in ROLES:
            log.error("API_KEYS: entry %r has no name or an unknown role and was ignored", name)
        elif len(key) < MIN_KEY_CHARS:
            log.error("API_KEYS: the key for %r is shorter than %d characters and was ignored", name, MIN_KEY_CHARS)
        elif key in seen:
            log.error("API_KEYS: the key for %r duplicates another entry and was ignored", name)
        else:
            seen.add(key)
            keys.append((key, Principal(name, role)))
    return keys


def auth_status() -> str:
    if auth_disabled():
        return "disabled"
    return "configured" if configured_keys() else "not_configured"


def authenticate(authorization: Optional[str]) -> Principal:
    if auth_disabled():
        return ANONYMOUS
    keys = configured_keys()
    if not keys:
        raise HTTPException(status_code=503, detail="API authentication is not configured on this server.")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    match: Optional[Principal] = None
    for key, principal in keys:  # compare against every key, so timing does not reveal which one matched
        if hmac.compare_digest(key.encode(), token.encode()) and token:
            match = principal
    if match is None:
        raise HTTPException(status_code=401, detail="Missing or invalid API key.", headers={"WWW-Authenticate": "Bearer"})
    return match


def require(*roles: str):
    """A FastAPI dependency that admits only the given roles."""

    def dependency(authorization: Optional[str] = Header(default=None)) -> Principal:
        principal = authenticate(authorization)
        if principal.role not in roles:
            raise HTTPException(status_code=403, detail="This key is not allowed to do that.")
        return principal

    return dependency
