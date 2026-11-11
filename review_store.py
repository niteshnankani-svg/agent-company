"""The review inbox: which paused runs are waiting for a person.

The checkpointer holds each paused run's full state. This table is the index over it, so "list
pending reviews" is one indexed query instead of a scan of every checkpoint. It lives in the same
database as the checkpoints (SQLite file locally, Postgres when CHECKPOINT_DB_URL is set).

Status moves pending -> resuming -> resolved. ``claim`` is an atomic UPDATE, so if two reviewers
(or one double click) try to resume the same run, exactly one wins; the other is told it is taken.
If the resume fails, ``release`` puts it back to pending so it can be retried.
"""
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS reviews (
        run_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        department TEXT NOT NULL,
        reason TEXT NOT NULL,
        question TEXT NOT NULL,
        has_draft INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        action TEXT,
        reviewer TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS reviews_status_created ON reviews (status, created_at)",
]
_COLUMNS = ["run_id", "request_id", "department", "reason", "question", "has_draft", "status", "created_at", "resolved_at", "action", "reviewer"]
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM reviews"
MAX_QUESTION_CHARS = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _SqliteDb:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def run(self, sql: str, params: tuple = ()) -> tuple[list[tuple], int]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall() if cur.description else [], cur.rowcount

    def close(self) -> None:
        self._conn.close()


class _PostgresDb:
    """Uses a psycopg connection pool; works with dict-row or tuple-row pools."""

    def __init__(self, pool: Any):
        self._pool = pool

    def run(self, sql: str, params: tuple = ()) -> tuple[list[tuple], int]:
        with self._pool.connection() as conn:
            cur = conn.execute(sql.replace("?", "%s"), params)
            rows = cur.fetchall() if cur.description else []
            return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in rows], cur.rowcount

    def close(self) -> None:  # the pool is owned by the runtime
        pass


class ReviewStore:
    def __init__(self, db: Any):
        self._db = db
        for statement in _SCHEMA:
            self._db.run(statement)

    @classmethod
    def sqlite(cls, path: str) -> "ReviewStore":
        return cls(_SqliteDb(path))

    @classmethod
    def postgres(cls, pool: Any) -> "ReviewStore":
        return cls(_PostgresDb(pool))

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _row(values: tuple) -> dict:
        row = dict(zip(_COLUMNS, values))
        row["has_draft"] = bool(row["has_draft"])
        return row

    def add(self, run_id: str, request_id: str, department: str, reason: str, question: str, has_draft: bool) -> None:
        """Queue a paused run. Safe to call twice for the same run."""
        self._db.run(
            "INSERT INTO reviews (run_id, request_id, department, reason, question, has_draft, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?) ON CONFLICT (run_id) DO NOTHING",
            (run_id, request_id, department, reason, question[:MAX_QUESTION_CHARS], int(has_draft), _now()),
        )

    def get(self, run_id: str) -> Optional[dict]:
        rows, _ = self._db.run(_SELECT + " WHERE run_id = ?", (run_id,))
        return self._row(rows[0]) if rows else None

    def list_pending(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows, _ = self._db.run(
            _SELECT + " WHERE status = 'pending' ORDER BY created_at ASC, run_id ASC LIMIT ? OFFSET ?", (limit, offset)
        )
        return [self._row(r) for r in rows]

    def count_pending(self) -> int:
        rows, _ = self._db.run("SELECT COUNT(*) FROM reviews WHERE status = 'pending'")
        return int(rows[0][0])

    def claim(self, run_id: str) -> bool:
        """Atomically take a pending review for resuming. True only for the one caller that wins."""
        _, n = self._db.run("UPDATE reviews SET status = 'resuming' WHERE run_id = ? AND status = 'pending'", (run_id,))
        return n == 1

    def release(self, run_id: str) -> None:
        self._db.run("UPDATE reviews SET status = 'pending' WHERE run_id = ? AND status = 'resuming'", (run_id,))

    def resolve(self, run_id: str, action: str, reviewer: str) -> None:
        self._db.run(
            "UPDATE reviews SET status = 'resolved', resolved_at = ?, action = ?, reviewer = ? WHERE run_id = ?",
            (_now(), action, reviewer, run_id),
        )
