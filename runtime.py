"""The compiled graph with its durable storage, created on first use (never at import).

Storage: SQLite file by default (``CHECKPOINT_DB_PATH``, default ``data/checkpoints.sqlite``), or
Postgres when ``CHECKPOINT_DB_URL`` is set. The checkpointer saves every run's state, which is what
lets a run pause at the human node and resume later, even after a restart. The review inbox
(review_store.py) lives in the same database.

Checkpoints contain the user's request and the evidence, so the database holds sensitive data at rest.
"""
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from clients import env_int
from graph import build_graph
from review_store import ReviewStore

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    graph: Any
    reviews: ReviewStore
    backend: str  # "sqlite" | "postgres"
    _close: Callable[[], None]

    def close(self) -> None:
        self._close()


_runtime: Optional[Runtime] = None
_lock = threading.Lock()


def _sqlite_runtime(path: str) -> Runtime:
    from langgraph.checkpoint.sqlite import SqliteSaver

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    saver = SqliteSaver(conn)
    saver.setup()
    reviews = ReviewStore.sqlite(path)
    graph = build_graph(saver, reviews)

    def close() -> None:
        reviews.close()
        conn.close()

    return Runtime(graph, reviews, "sqlite", close)


def _postgres_runtime(url: str) -> Runtime:
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        conninfo=url,
        max_size=env_int("CHECKPOINT_POOL_SIZE", 10),
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
    )
    saver = PostgresSaver(pool)
    saver.setup()
    reviews = ReviewStore.postgres(pool)
    graph = build_graph(saver, reviews)
    return Runtime(graph, reviews, "postgres", pool.close)


def build_runtime() -> Runtime:
    url = os.environ.get("CHECKPOINT_DB_URL", "").strip()
    if url:
        log.info("checkpoints: Postgres")  # never log the URL: it carries credentials
        return _postgres_runtime(url)
    path = os.environ.get("CHECKPOINT_DB_PATH", "data/checkpoints.sqlite")
    log.info("checkpoints: SQLite file %s", path)
    return _sqlite_runtime(path)


def get_runtime() -> Runtime:
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = build_runtime()
        return _runtime


def reset_runtime() -> None:
    """Close and forget the runtime. Tests use it, and it models a process restart."""
    global _runtime
    with _lock:
        if _runtime is not None:
            _runtime.close()
        _runtime = None
