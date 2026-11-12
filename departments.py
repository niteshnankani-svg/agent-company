"""The five departments behind one uniform call.

Every runner has the signature ``(raw_input, fields, sub_department, follow_up, feedback) -> DepartmentResult``
and touches no graph state. The graph turns a result into a state update; tests
and the sequential runner call these directly.
"""
import concurrent.futures
import logging
import threading
from typing import Callable, Optional

from clients import env_float, env_int

from backend_ticketing import run_backend
from contract import DepartmentResult
from export_compliance import run_export
from finance import run_finance
from hr_complaints import run_hr
from legal_compliance import run_legal

log = logging.getLogger(__name__)

Runner = Callable[[str, Optional[dict], Optional[str], bool, Optional[str]], DepartmentResult]

RUNNERS: dict[str, Runner] = {
    "finance": lambda raw, fields, sub, follow_up, feedback: run_finance(raw, fields, sub, follow_up),
    "hr_complaints": lambda raw, fields, sub, follow_up, feedback: run_hr(raw, fields, feedback),
    "backend_ticketing": lambda raw, fields, sub, follow_up, feedback: run_backend(raw),
    "export_compliance": lambda raw, fields, sub, follow_up, feedback: run_export(raw, fields, feedback),
    "legal_compliance": lambda raw, fields, sub, follow_up, feedback: run_legal(raw, fields, feedback),
}

DEPARTMENT_NAMES = tuple(RUNNERS)


_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
_pool_lock = threading.Lock()


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=env_int("DEPARTMENT_WORKERS", 64), thread_name_prefix="department"
            )
        return _pool


def department_timeout() -> float:
    """Upper bound, in seconds, for one department attempt. Must exceed the sum of its service timeouts
    (Export makes two calls: 60 s + 90 s by default)."""
    return env_float("DEPARTMENT_TIMEOUT_SECONDS", 180.0)


def run_department(
    name: str,
    raw_input: str,
    fields: Optional[dict],
    sub_department: Optional[str] = None,
    follow_up: bool = False,
    feedback: Optional[str] = None,
) -> DepartmentResult:
    """Run one department; anything that goes wrong becomes a ``failed`` result, never a crash or a hang.

    Departments handle their own known failures (service down, bad data). This is the net for the
    unknown: an unexpected exception is logged with its traceback and the caller sees only its type; a
    department that does not finish within ``DEPARTMENT_TIMEOUT_SECONDS`` is abandoned and reported as
    timed out. (A Python thread cannot be killed, so an abandoned call finishes in the background when its
    own service timeout fires. If every worker is stuck, new attempts wait and then time out too.)
    """
    runner = RUNNERS.get(name)
    if runner is None:
        return DepartmentResult(
            department=name, status="failed", error=f"no department named '{name}'"
        )
    deadline = department_timeout()
    future = _executor().submit(runner, raw_input, fields, sub_department, follow_up, feedback)
    done, _ = concurrent.futures.wait([future], timeout=deadline)
    if not done:
        future.cancel()
        log.error("department %s did not finish within %ss", name, deadline)
        return DepartmentResult(
            department=name, status="failed",
            error=f"timed_out: {name} did not finish within {deadline:g}s",
        )
    try:
        return future.result()
    except Exception as exc:
        log.error("department %s raised unexpectedly", name, exc_info=exc)
        return DepartmentResult(
            department=name, status="failed", error=f"unexpected_error: {type(exc).__name__}"
        )
