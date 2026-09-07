import asyncio

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from graph import build_graph, build_initial_state

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
            from state import new_state

            state = new_state(req.request_id, req.raw_input)
            state["department_sequence"] = req.department_sequence
            state["department_fields_by_dept"] = (
                req.department_fields_by_dept or {}
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
        result = await asyncio.to_thread(graph_app.invoke, state)

        return {
            "decision": result["department_output"]["decision"],
            "confidence": result["department_output"]["confidence"],
            "rationale": result["department_output"]["rationale"],
            "evidence_ids": result["department_output"]["evidence_ids"],
            "source": result["department_output"]["source"],
            "audit_trail": result["audit_trail"],
            "department_history": result.get("department_history"),
            "backend_triage": result.get("backend_triage"),
            "escalation_reason": result.get("escalation_reason"),
            "needs_human": result["department_output"]["decision"] == "human_escalation" or bool(result.get("escalation_reason")),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Request processing failed: {str(e)}",
        )


@app.get("/health")
async def health():
    return {"status": "ok"}
