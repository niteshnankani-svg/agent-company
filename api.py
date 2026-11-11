import asyncio

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from contract import DepartmentResult
from graph import build_graph, build_initial_state
from state import new_state

app = FastAPI(title="Agent Company API", version="1.0.0")

# Build the graph ONCE at startup, not per-request — matches how every
# department's local models (BERT, XGBoost) already load once at import
# time, not per call.
graph_app = build_graph()


class DepartmentRequest(BaseModel):
    request_id: str
    raw_input: str
    department_fields: Optional[dict] = None
    sub_department: Optional[str] = None
    # For sequential multi-department requests (proven working on the
    # Export -> Finance scenario earlier this session):
    department_sequence: Optional[list[str]] = None
    department_fields_by_dept: Optional[dict] = None


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
