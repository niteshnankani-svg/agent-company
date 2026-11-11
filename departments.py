"""The five departments behind one uniform call.

Every runner has the signature ``(raw_input, fields, sub_department, follow_up, feedback) -> DepartmentResult``
and touches no graph state. The graph turns a result into a state update; tests
and the sequential runner call these directly.
"""
import logging
from typing import Callable, Optional

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


def run_department(
    name: str,
    raw_input: str,
    fields: Optional[dict],
    sub_department: Optional[str] = None,
    follow_up: bool = False,
    feedback: Optional[str] = None,
) -> DepartmentResult:
    """Run one department; an unexpected exception becomes a ``failed`` result.

    Departments handle their own known failures (service down, bad data). This is
    the net for the unknown: the graph keeps going, the failure is logged with its
    traceback, and the caller sees only the exception type, never its message.
    """
    runner = RUNNERS.get(name)
    if runner is None:
        return DepartmentResult(
            department=name, status="failed", error=f"no department named '{name}'"
        )
    try:
        return runner(raw_input, fields, sub_department, follow_up, feedback)
    except Exception as exc:
        log.exception("department %s raised unexpectedly", name)
        return DepartmentResult(
            department=name, status="failed", error=f"unexpected_error: {type(exc).__name__}"
        )
