import asyncio

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional

from contract import DepartmentResult
from departments import DEPARTMENT_NAMES
from graph import build_graph, build_initial_state
from state import new_state

app = FastAPI(title="Agent Company API", version="1.0.0")

# Build the graph ONCE at startup, not per-request — matches how every
# department's local models (BERT, XGBoost) already load once at import
# time, not per call.
graph_app = build_graph()


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


@app.post("/request")
async def handle_request(req: DepartmentRequest):
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

        # Department nodes use synchronous wrappers around some async tools
        # (for example asyncio.run() in Export). Keep the HTTP handler async,
        # but invoke the synchronous graph off the event-loop thread.
        final = await asyncio.to_thread(graph_app.invoke, state)

        raw_result = final.get("department_result")
        result = (
            DepartmentResult.from_state(raw_result)
            if raw_result
            else DepartmentResult(
                department="orchestrator", status="failed", error="no result was produced"
            )
        )
        return {
            "request_id": final["request_id"],
            "result": result.to_state(),
            "needs_human": result.status == "needs_human",
            "verification": final.get("verification"),
            "escalation_reason": final.get("escalation_reason"),
            "department_history": final.get("department_history") or [],
            "audit_trail": final["audit_trail"],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Request processing failed: {str(e)}",
        )


@app.get("/health")
async def health():
    return {"status": "ok"}
