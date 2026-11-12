import asyncio
import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from typing import Optional

from auth import ANONYMOUS, Principal, require
from contract import DepartmentResult
from departments import DEPARTMENT_NAMES
from graph import build_initial_state
from ready import readiness
from review import HumanDecision, has_draft
from runtime import get_runtime
from state import new_state

log = logging.getLogger(__name__)

CLIENT = Depends(require("client", "reviewer"))
REVIEWER = Depends(require("reviewer"))

app = FastAPI(title="Agent Company API", version="1.0.0")



def _incident(exc: BaseException, where: str) -> str:
    """Log the real error with a short id and return that id. The caller only ever sees the id."""
    incident = uuid.uuid4().hex[:10]
    log.error("incident %s in %s", incident, where, exc_info=exc)
    return incident


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    incident = _incident(exc, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Something went wrong on our side. Incident {incident}."})


# The graph and its storage are created on first use (runtime.py), not at import, so importing this
# module needs no database and no keys.

class FollowUpIn(BaseModel):
    """The answer to a form the system generated (a ``needs_more_info`` result's ``follow_up``).

    It carries the department that form came from so the request is not routed a second time.
    """

    department: str
    sub_department: Optional[str] = None
    fields: dict

    @field_validator("department")
    @classmethod
    def known_department(cls, v: str) -> str:
        if v not in DEPARTMENT_NAMES:
            raise ValueError(f"unknown department {v!r}")
        return v


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    note: Optional[str] = None
    answer: Optional[str] = None
    reviewer: Optional[str] = None  # honoured only when authentication is disabled


class DepartmentRequest(BaseModel):
    request_id: str
    raw_input: str
    department_fields: Optional[dict] = None
    sub_department: Optional[str] = None
    # For sequential multi-department requests (proven working on the
    # Export -> Finance scenario earlier this session):
    department_sequence: Optional[list[str]] = None
    department_fields_by_dept: Optional[dict] = None
    # Set by the console when the user submits a follow-up form.
    follow_up: Optional[FollowUpIn] = None


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _response(run_id: str, snapshot) -> dict:
    """The API's answer for a run, from its latest saved state. ``pending_review`` is true while the
    run is paused waiting for a person."""
    values = snapshot.values
    raw = values.get("department_result")
    result = (
        DepartmentResult.from_state(raw)
        if raw
        else DepartmentResult(department="orchestrator", status="failed", error="no result was produced")
    )
    pending = bool(snapshot.next)
    return {
        "run_id": run_id,
        "request_id": values.get("request_id"),
        "result": result.to_state(),
        "needs_human": result.status == "needs_human",
        "pending_review": pending,
        "verification": values.get("verification"),
        "escalation_reason": values.get("escalation_reason"),
        "department_history": values.get("department_history") or [],
        "audit_trail": values.get("audit_trail") or [],
    }


@app.post("/request")
async def handle_request(req: DepartmentRequest, principal: Principal = CLIENT):
    try:
        if req.department_sequence:
            state = new_state(
                req.request_id,
                req.raw_input,
                department_sequence=req.department_sequence,
                department_fields_by_dept=req.department_fields_by_dept or {},
            )
        elif req.follow_up:
            state = new_state(
                req.request_id,
                req.raw_input,
                department_fields=req.follow_up.fields,
                sub_department=req.follow_up.sub_department,
                pinned_department=req.follow_up.department,
                follow_up=True,
            )
        else:
            state = build_initial_state(
                req.request_id,
                req.raw_input,
                req.department_fields or {},
                req.sub_department,
            )

        runtime = get_runtime()
        # The run id is generated here, never taken from the client: a client-chosen id could collide
        # with an existing run's saved state.
        run_id = uuid.uuid4().hex
        # Department nodes use synchronous wrappers around some async tools
        # (for example asyncio.run() in Export). Keep the HTTP handler async,
        # but invoke the synchronous graph off the event-loop thread.
        await asyncio.to_thread(runtime.graph.invoke, state, _config(run_id))
        return _response(run_id, runtime.graph.get_state(_config(run_id)))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"The request could not be processed. Incident {_incident(exc, '/request')}.",
        )


@app.get("/reviews")
async def list_reviews(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), principal: Principal = REVIEWER):
    """Runs waiting for a person, oldest first."""
    reviews = get_runtime().reviews
    return {
        "count": reviews.count_pending(),
        "reviews": [
            {
                "run_id": r["run_id"],
                "request_id": r["request_id"],
                "department": r["department"],
                "reason": r["reason"],
                "question": r["question"][:500],
                "has_draft": r["has_draft"],
                "created_at": r["created_at"],
            }
            for r in reviews.list_pending(limit, offset)
        ],
    }


@app.get("/runs/{run_id}")
async def get_run(run_id: str, principal: Principal = CLIENT):
    """Where a run stands: the result so far (the draft and evidence a reviewer needs, or the final
    outcome once decided) and whether it is still waiting for a person."""
    runtime = get_runtime()
    snapshot = await asyncio.to_thread(runtime.graph.get_state, _config(run_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="No such run.")
    return _response(run_id, snapshot)


@app.post("/reviews/{run_id}/resume")
async def resume_review(run_id: str, body: ResumeBody, principal: Principal = REVIEWER):
    """A person's decision on a paused run: approve the draft, write the answer, or reject.

    The reviewer recorded is the name of the API key used; it cannot be chosen by the caller. (Only with
    authentication explicitly disabled, for local development, is a name taken from the body.)
    """
    reviewer = principal.name if principal is not ANONYMOUS else (body.reviewer or "")
    try:
        decision = HumanDecision(action=body.action, reviewer=reviewer, note=body.note, answer=body.answer)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=[{"msg": e["msg"], "loc": list(e["loc"])} for e in exc.errors()])
    runtime = get_runtime()
    config = _config(run_id)
    snapshot = await asyncio.to_thread(runtime.graph.get_state, config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="No such run.")
    if tuple(snapshot.next) != ("human_review",):
        raise HTTPException(status_code=409, detail="This run is not waiting for a review.")
    held = DepartmentResult.from_state(snapshot.values["department_result"])
    if decision.action == "approve" and not has_draft(held):
        raise HTTPException(status_code=422, detail="There is no draft answer to approve; write an answer or reject.")

    # Atomic: of two simultaneous resumes, exactly one gets to run.
    if not await asyncio.to_thread(runtime.reviews.claim, run_id):
        raise HTTPException(status_code=409, detail="This review has already been taken or decided.")
    try:
        await asyncio.to_thread(runtime.graph.invoke, Command(resume=decision.model_dump()), config)
    except Exception:
        log.exception("resuming run %s failed", run_id)
        await asyncio.to_thread(runtime.reviews.release, run_id)
        raise HTTPException(status_code=500, detail="Could not apply the decision. It is still pending; try again.")
    await asyncio.to_thread(runtime.reviews.resolve, run_id, decision.action, decision.reviewer)
    return _response(run_id, await asyncio.to_thread(runtime.graph.get_state, config))


@app.get("/ready")
async def ready():
    """Can this process usefully take requests, and which departments can do their job right now?"""
    report, code = await readiness(get_runtime)
    return JSONResponse(status_code=code, content=report)


@app.get("/health")
async def health():
    return {"status": "ok"}
