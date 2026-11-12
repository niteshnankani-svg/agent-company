"""Semantic answer cache, with a policy for what may never be cached.

Why this replaces the old ``difflib`` cache: string similarity returned a Sales
headcount for a Legal question (similarity 0.936). Embeddings fix paraphrases but
NOT that class of bug: a sentence embedding of "Section 357" and "Section 358",
or "under BNS" and "under BNSS", is nearly identical. So similarity is the last
check, not the only one. A cached answer is reused only when ALL of these hold:

1. The department is declared cacheable (its answers come from a fixed corpus).
2. The request text has no personal identifiers and no first-person wording.
3. Same department and same scope (for Legal, the act filter).
4. The *discriminators* match exactly: every number/identifier, all-caps acronym,
   and mid-sentence capitalised word. This is what separates 357 from 358.
5. The entry has not expired (TTL).
6. Embedding cosine similarity is at least the threshold.

Only ``answered`` results that passed the verifier are stored. Everything else
(needs_human, needs_more_info, failed) is never cached.

The embedding provider is a deployment choice (``EMBEDDING_PROVIDER``). With none
configured the cache is simply off, which is always safe. If the embedder fails,
the lookup is a miss and the request is answered normally; the cache never causes
an error.

Honest limits: the similarity threshold (default 0.95) is NOT calibrated, because
that needs the real embedding model and a labelled set of near-miss questions. The
cache lives in process memory, so it is per worker and lost on restart.
"""
import json
import logging
import os
import re
import string
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

import numpy as np

from clients import env_float, env_int
from contract import DepartmentResult
from pii import contains_pii

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# What may be cached
# ----------------------------------------------------------------------------
CACHEABLE_DEPARTMENTS = {"legal_compliance"}  # answers come from fixed statute text

NEVER_CACHE_REASONS = {
    "finance": "answers depend on a specific person's or invoice's data",
    "hr_complaints": "answers depend on live employee data",
    "backend_ticketing": "tickets are specific to a person and a moment",
    "export_compliance": "an HS-code answer must be checked against live TradeStat every time",
}

_FIRST_PERSON = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|me|my|mine|myself|we|our|ours)\b", re.I)


def why_not_cacheable(
    department: str, text: str, result: Optional[DepartmentResult] = None
) -> Optional[str]:
    """None if this exchange may be cached, otherwise the reason it may not."""
    if department not in CACHEABLE_DEPARTMENTS:
        return NEVER_CACHE_REASONS.get(department, "department is not declared cacheable")
    if result is not None and result.status != "answered":
        return f"status '{result.status}' is never cached"
    if contains_pii(text):
        return "request contains personal identifiers"
    if _FIRST_PERSON.search(text):
        return "request refers to a specific person or situation"
    return None


# ----------------------------------------------------------------------------
# Discriminators: what must be identical for two questions to share an answer
# ----------------------------------------------------------------------------
_HAS_DIGIT = re.compile(r"[A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*")
_ACRONYM = re.compile(r"\b[A-Z]{2,}\b")


def discriminators(text: str) -> frozenset[str]:
    found = {m.group(0).lower().strip(".-") for m in _HAS_DIGIT.finditer(text)}
    found |= {m.group(0) for m in _ACRONYM.finditer(text)}
    for sentence in re.split(r"(?<=[.?!])\s+", text):
        for word in sentence.split()[1:]:  # the first word of a sentence is capitalised anyway
            w = word.strip(string.punctuation)
            if w[:1].isupper() and not w.isupper():
                found.add(w.lower())
    return frozenset(found)


# ----------------------------------------------------------------------------
# Embedders
# ----------------------------------------------------------------------------
class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """A local sentence-transformers model. No key, but a large optional dependency."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None

    def embed(self, text: str) -> list[float]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model.encode(text, normalize_embeddings=True).tolist()


def embedder_from_env() -> tuple[Optional[Embedder], str]:
    """(embedder, note). With no provider configured the embedder is None and the cache is off."""
    provider = os.environ.get("EMBEDDING_PROVIDER", "").strip().lower()
    if not provider:
        return None, "EMBEDDING_PROVIDER is not set"
    if provider == "local":
        return LocalEmbedder(os.environ.get("EMBEDDING_MODEL_ID", "sentence-transformers/all-MiniLM-L6-v2")), "local"
    log.error("Unknown EMBEDDING_PROVIDER %r (expected 'local'); cache disabled", provider)
    return None, f"unknown EMBEDDING_PROVIDER {provider!r}"


# ----------------------------------------------------------------------------
# The cache
# ----------------------------------------------------------------------------
@dataclass
class Hit:
    result: dict
    similarity: float
    age_seconds: float


@dataclass
class _Entry:
    department: str
    scope: str
    discriminators: frozenset
    vector: np.ndarray
    result: dict
    stored_at: float


def _unit(vector: list[float]) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if v.ndim != 1 or v.size == 0 or not np.isfinite(norm) or norm == 0.0:
        raise ValueError("embedding is not a usable vector")
    return v / norm


class SemanticCache:
    def __init__(
        self,
        embedder: Embedder,
        *,
        ttl_seconds: float = 86400.0,
        threshold: float = 0.95,
        max_entries: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.embedder = embedder
        self.ttl = ttl_seconds
        self.threshold = threshold
        self.max_entries = max_entries
        self._clock = clock
        self._entries: "OrderedDict[int, _Entry]" = OrderedDict()
        self._next_id = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def _expire(self, now: float) -> None:
        for key in [k for k, e in self._entries.items() if now - e.stored_at > self.ttl]:
            del self._entries[key]

    def lookup(self, department: str, text: str, scope: str = "") -> Optional[Hit]:
        if why_not_cacheable(department, text) is not None:
            return None
        try:
            query = _unit(self.embedder.embed(text))
        except Exception as exc:  # the embedder is a dependency; its failure is a miss
            log.warning("cache lookup skipped: embedder failed (%s)", type(exc).__name__)
            return None
        wanted = discriminators(text)
        now = self._clock()
        with self._lock:
            self._expire(now)
            candidates = [
                (k, e) for k, e in self._entries.items()
                if e.department == department and e.scope == scope
                and e.discriminators == wanted and e.vector.shape == query.shape
            ]
            if not candidates:
                return None
            sims = np.stack([e.vector for _, e in candidates]) @ query
            best = int(np.argmax(sims))
            similarity = float(sims[best])
            if similarity < self.threshold:
                return None
            key, entry = candidates[best]
            self._entries.move_to_end(key)  # recently used
            return Hit(result=entry.result, similarity=similarity, age_seconds=now - entry.stored_at)

    def store(self, department: str, text: str, result: DepartmentResult, scope: str = "") -> Optional[str]:
        """Returns None if stored, otherwise the reason it was not."""
        reason = why_not_cacheable(department, text, result)
        if reason:
            return reason
        try:
            vector = _unit(self.embedder.embed(text))
        except Exception as exc:
            log.warning("cache store skipped: embedder failed (%s)", type(exc).__name__)
            return "embedder unavailable"
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._entries[self._next_id] = _Entry(
                department, scope, discriminators(text), vector, result.to_state(), now
            )
            self._next_id += 1
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)  # evict least recently used
        return None


# ----------------------------------------------------------------------------
# The process-wide cache used by the graph
# ----------------------------------------------------------------------------
_cache: Optional[SemanticCache] = None
_cache_note = "not initialised"
_cache_ready = False
_init_lock = threading.Lock()


def get_cache() -> tuple[Optional[SemanticCache], str]:
    """(cache or None, note explaining why it is off). Built from the environment on first use."""
    global _cache, _cache_note, _cache_ready
    with _init_lock:
        if not _cache_ready:
            embedder, note = embedder_from_env()
            if embedder is not None:
                _cache = SemanticCache(
                    embedder,
                    ttl_seconds=env_float("CACHE_TTL_SECONDS", 86400.0),
                    threshold=env_float("CACHE_SIMILARITY_THRESHOLD", 0.95),
                    max_entries=env_int("CACHE_MAX_ENTRIES", 1000),
                )
            _cache_note, _cache_ready = note, True
        return _cache, _cache_note


def set_cache(cache: Optional[SemanticCache], note: str = "set explicitly") -> None:
    """Install (or, with None, disable) the process-wide cache. Used by tests."""
    global _cache, _cache_note, _cache_ready
    with _init_lock:
        _cache, _cache_note, _cache_ready = cache, note, True
