"""The eval logic, separate from the command line so it can be tested without any live service.

Nothing here invents a result: a call that could not be made is recorded as ERROR (never as a pass), and
a check that cannot be evaluated is a failed check with the reason.
"""
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from cases import BREAKABLE, CASES, DEPARTMENTS, NOT_APPLICABLE, STATUSES

REPO_ROOT = Path(__file__).resolve().parent.parent

# Anything that looks like internals in a response is a failed check on every case.
LEAK_PATTERNS = [
    re.compile(p, re.I)
    for p in (r"traceback \(most recent", r"\bFile \"[^\"]+\.py\"", r"\[Errno \d+\]", r"password\s*[=:]", r"api[_-]?key\s*[=:]\s*\S{8,}", r"/srv/|/app/\w+\.py")
]


# ------------------------------------------------------------------ coverage
def covered_cells(cases: list[dict]) -> set[tuple[str, str]]:
    cells = set()
    for c in cases:
        if c.get("informational") or not c.get("department"):
            continue
        if c.get("status"):
            cells.add((c["department"], c["status"]))
        if c.get("then", {}).get("status"):
            cells.add((c["department"], c["then"]["status"]))
    return cells


def coverage_gaps(cases: list[dict]) -> list[tuple[str, str]]:
    """Cells of department x status that have no case and are not declared impossible."""
    covered = covered_cells(cases)
    return [(d, s) for d in DEPARTMENTS for s in STATUSES if (d, s) not in covered and (d, s) not in NOT_APPLICABLE]


# -------------------------------------------------------------------- checks
@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


def _text(body: dict) -> str:
    return " ".join(str(v) for v in (body.get("result", {}).get("answer"), body.get("result", {}).get("error")) if v)


def check_response(expect: dict, body: dict) -> list[Check]:
    """Deterministic checks of one API response against one set of expectations."""
    result = body.get("result") or {}
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, bool(ok), detail))

    if expect.get("status"):
        add("status", result.get("status") == expect["status"], f"expected {expect['status']}, got {result.get('status')}")
    if expect.get("department"):
        add("department", result.get("department") == expect["department"], f"expected {expect['department']}, got {result.get('department')}")
    if expect.get("reasons"):
        add("reason", result.get("reason") in expect["reasons"], f"expected one of {expect['reasons']}, got {result.get('reason')}")
    if expect.get("verification"):
        got = (body.get("verification") or {}).get("status")
        add("verification", got in expect["verification"], f"expected one of {expect['verification']}, got {got}")
    answer = (result.get("answer") or "").lower()
    for word in expect.get("answer_contains", []):
        add(f"answer contains '{word}'", word.lower() in answer, "not found in the answer")
    for word in expect.get("answer_not_contains", []):
        add(f"answer does not contain '{word}'", word.lower() not in answer, "found in the answer")
    if expect.get("status") == "answered":
        has_evidence = any((e.get("text") or e.get("data")) for e in result.get("evidence", []))
        add("evidence present", has_evidence, "an answered result must carry the evidence it relied on")
    missing = result.get("missing_fields", [])
    for name in expect.get("missing_fields_include", []):
        add(f"asks for '{name}'", name in missing, f"asked for {missing}")
    for name in expect.get("missing_fields_exclude", []):
        add(f"does not ask for '{name}'", name not in missing, f"asked for {missing}")
    if expect.get("error_starts_with"):
        err = result.get("error") or ""
        add("error says why", err.startswith(expect["error_starts_with"]), f"expected to start with '{expect['error_starts_with']}', got '{err}'")
    leaked = [p.pattern for p in LEAK_PATTERNS if p.search(str(body))]
    add("nothing internal leaked", not leaked, f"matched {leaked}")
    return checks


def check_pair(expect: dict, bodies: list[dict]) -> list[Check]:
    checks = []
    answers = [(b.get("result", {}).get("answer") or "") for b in bodies]
    for text, word in zip(answers, expect.get("each_answer_contains", [])):
        checks.append(Check(f"answer mentions '{word}'", word.lower() in text.lower(), "not found in its own answer"))
    if expect.get("answers_differ"):
        checks.append(Check("the two answers differ", len(answers) == 2 and answers[0].strip() != answers[1].strip(), "identical answers: one was reused for the other"))
    return checks


# -------------------------------------------------------------------- the API
class ApiCallError(Exception):
    pass


class Api:
    def __init__(self, base_url: str, key: Optional[str] = None, timeout: float = 120.0, transport: Any = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._headers = {"Authorization": f"Bearer {key}"} if key else {}

    def request(self, raw_input: str, follow_up: Optional[dict] = None) -> dict:
        payload: dict = {"request_id": "eval-" + os.urandom(4).hex(), "raw_input": raw_input}
        if follow_up:
            payload["follow_up"] = follow_up
        try:
            response = self._client.post(f"{self.base_url}/request", json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ApiCallError(f"could not reach the API: {type(exc).__name__}") from exc
        if response.status_code == 401:
            raise ApiCallError("the API rejected the key (401): set EVAL_API_KEY to a valid client or reviewer key")
        if response.status_code == 503:
            raise ApiCallError("the API answered 503 (authentication or storage is not set up; see /ready)")
        if response.status_code >= 400:
            raise ApiCallError(f"the API answered HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ApiCallError("the API reply was not JSON") from exc

    def close(self) -> None:
        self._client.close()


# ------------------------------------------------------------------ running cases
@dataclass
class Step:
    label: str
    request: str
    response: Optional[dict] = None
    checks: list[Check] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class CaseResult:
    case: dict
    steps: list[Step] = field(default_factory=list)
    error: Optional[str] = None
    judge: Optional[dict] = None

    @property
    def outcome(self) -> str:
        if self.error or any(s.error for s in self.steps):
            return "ERROR"
        if self.case.get("informational"):
            return "INFO"
        return "PASS" if all(c.passed for s in self.steps for c in s.checks) else "FAIL"


def run_case(api: Api, case: dict) -> CaseResult:
    out = CaseResult(case)
    try:
        if case.get("raw_inputs"):
            bodies = []
            for i, text in enumerate(case["raw_inputs"], 1):
                body = api.request(text)
                bodies.append(body)
                out.steps.append(Step(f"request {i}", text, body, check_response(case, body)))
            out.steps.append(Step("pair", " / ".join(case["raw_inputs"]), None, check_pair(case["pair"], bodies)))
            return out

        body = api.request(case["raw_input"])
        step = Step("request", case["raw_input"], body, check_response(case, body))
        out.steps.append(step)
        if case.get("follow_up_answers") and (body.get("result") or {}).get("status") == "needs_more_info":
            form = (body["result"].get("follow_up") or {})
            fields = {**form.get("collected", {}), **case["follow_up_answers"]}
            body2 = api.request(case["raw_input"], follow_up={
                "department": body["result"]["department"], "sub_department": form.get("sub_department"), "fields": fields,
            })
            out.steps.append(Step("follow-up (answers submitted)", case["raw_input"], body2, check_response(case.get("then", {}), body2)))
        elif case.get("follow_up_answers"):
            step.checks.append(Check("form was offered", False, "no needs_more_info result to answer, so the follow-up was not run"))
    except ApiCallError as exc:
        out.error = str(exc)
    return out


# -------------------------------------------------------- failure-injection servers
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def failure_env(base: dict, broken: list[str], key: str, db_path: str) -> dict:
    """Environment for a local API whose chosen services point at an address nothing listens on."""
    unknown = [b for b in broken if b not in BREAKABLE]
    if unknown:
        raise ValueError(f"cannot break unknown service address(es): {unknown}")
    env = dict(base)
    for name in broken:
        env[name] = "http://127.0.0.1:9/"  # the discard port: connection refused immediately
    env.pop("AGENT_AUTH_DISABLED", None)
    env["API_KEYS"] = f"eval:client:{key}"
    env["CHECKPOINT_DB_PATH"] = db_path
    env.pop("CHECKPOINT_DB_URL", None)
    return env


class FailureServers:
    """Starts one local API per distinct set of broken services, and stops them all on exit."""

    def __init__(self, python: str = sys.executable, root: Path = REPO_ROOT, start_timeout: float = 40.0):
        self.python, self.root, self.start_timeout = python, root, start_timeout
        self._servers: dict[frozenset, tuple[subprocess.Popen, str, str, str]] = {}
        self._tmp = tempfile.TemporaryDirectory(prefix="eval-failure-")

    def url_and_key(self, broken: list[str]) -> tuple[str, str]:
        key_ = frozenset(broken)
        if key_ not in self._servers:
            self._servers[key_] = self._start(sorted(key_))
        _, url, key, _ = self._servers[key_]
        return url, key

    def _start(self, broken: list[str]):
        port = free_port()
        key = os.urandom(24).hex()
        db = str(Path(self._tmp.name) / f"{port}.sqlite")
        env = failure_env(dict(os.environ), broken, key, db)
        log = open(Path(self._tmp.name) / f"{port}.log", "wb")
        proc = subprocess.Popen(
            [self.python, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=self.root, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"the local API exited while starting (see {log.name})")
            try:
                if httpx.get(f"{url}/health", timeout=1.0).status_code == 200:
                    return proc, url, key, str(log.name)
            except httpx.HTTPError:
                time.sleep(0.3)
        proc.terminate()
        raise RuntimeError("the local API did not become healthy in time")

    def close(self) -> None:
        for proc, *_ in self._servers.values():
            proc.terminate()
        for proc, *_ in self._servers.values():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._servers.clear()
        self._tmp.cleanup()

    def __enter__(self) -> "FailureServers":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ------------------------------------------------------------------- the report
def matrix_rows(results: list[CaseResult]) -> list[str]:
    lines = ["| Department | answered | needs_human | needs_more_info | failed |", "|---|---|---|---|---|"]
    for d in DEPARTMENTS:
        cells = []
        for s in STATUSES:
            if (d, s) in NOT_APPLICABLE:
                cells.append("n/a")
                continue
            relevant = [r for r in results if r.case.get("department") == d and s in (r.case.get("status"), r.case.get("then", {}).get("status")) and not r.case.get("informational")]
            if not relevant:
                cells.append("**GAP**" if (d, s) not in covered_cells(CASES) else "not run")
                continue
            passed = sum(1 for r in relevant if r.outcome == "PASS")
            errors = sum(1 for r in relevant if r.outcome == "ERROR")
            cells.append(f"{passed}/{len(relevant)}" + (f" ({errors} error)" if errors else ""))
        lines.append(f"| {d} | " + " | ".join(cells) + " |")
    return lines


def render_report(results: list[CaseResult], meta: dict) -> str:
    counts = {k: sum(1 for r in results if r.outcome == k) for k in ("PASS", "FAIL", "ERROR", "INFO")}
    lines = [
        "# Agent Company eval run",
        "",
        f"- **Run at**: {meta['run_at']}",
        f"- **API**: {meta['api']}",
        f"- **Judge**: {meta.get('judge') or 'none (deterministic checks only)'}",
        f"- **Cases**: {len(results)}: {counts['PASS']} passed, {counts['FAIL']} failed, {counts['ERROR']} could not run, {counts['INFO']} informational",
        "",
        "A case that could not run (ERROR) is not a pass. The optional judge never changes a pass or fail.",
        "",
        "## Coverage: cases passed / cases run, per department and result status",
        "",
        *matrix_rows(results),
        "",
        "n/a cells cannot happen by design:",
        *[f"- {d} / {s}: {why}" for (d, s), why in NOT_APPLICABLE.items()],
        "",
        "---",
        "",
    ]
    for r in results:
        c = r.case
        lines += [f"## {r.outcome}: {c['id']}", ""]
        if c.get("raw_inputs"):
            lines += [f"**Requests**: {' / '.join(c['raw_inputs'])}"]
        else:
            lines += [f"**Request**: {c['raw_input']}"]
        exp = [f"{k}=`{c[k]}`" for k in ("department", "status", "reasons", "verification") if c.get(k)]
        lines += [f"**Expected**: {', '.join(exp) or '(not scored)'}", f"**Testing**: {c.get('notes', '')}"]
        if c.get("ground_truth"):
            lines += [f"**Ground truth**: {c['ground_truth']}"]
        lines += [""]
        if r.error:
            lines += [f"**COULD NOT RUN**: {r.error}", "", "---", ""]
            continue
        for step in r.steps:
            lines += [f"### {step.label}"]
            if step.response:
                res = step.response.get("result", {})
                lines += [
                    f"- result: status `{res.get('status')}`, department `{res.get('department')}`, reason `{res.get('reason')}`, "
                    f"verification `{(step.response.get('verification') or {}).get('status')}`",
                ]
                if res.get("answer"):
                    lines += [f"- answer: {res['answer'][:600]}"]
                if res.get("error"):
                    lines += [f"- error: {res['error']}"]
                if res.get("missing_fields"):
                    lines += [f"- asked for: {', '.join(res['missing_fields'])}"]
            for chk in step.checks:
                lines += [f"- [{'x' if chk.passed else ' '}] {chk.name}" + ("" if chk.passed else f": {chk.detail}")]
            lines += [""]
        if r.judge:
            lines += ["**Judge (comment only)**: " + (r.judge.get("error") or f"score {r.judge['verdict'].get('score_1_to_5')}/5: {r.judge['verdict'].get('reasoning')}"), ""]
        lines += ["---", ""]
    return "\n".join(lines)
