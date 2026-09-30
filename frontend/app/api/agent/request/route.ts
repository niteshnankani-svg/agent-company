import { NextRequest, NextResponse } from "next/server";
import type { AgentResponse, AuditTrailEntry } from "@/lib/types";
import {
  localClassify,
  scrubPii,
  INJECTION_RE,
  SECURITY_RE,
  ROUTE_MARGIN,
  DEPT_NODE,
  DEPT_LABEL,
  type Department,
} from "@/lib/orchestrator";
import { predictHelpdesk } from "@/lib/helpdeskModel";

// This IS the backend. The multi-department orchestrator and the Backend /
// Ticketing IT-triage model both run in-process here (no external service, no
// GPU) — a faithful port of agent_company/orchestrator.py + backend_ticketing.py
// with the trained TF-IDF + logistic-regression weights from
// models/helpdesk/model.json. Finance / HR / Legal / Export keep their real
// separate backends, which aren't bundled into this deployment.
export const runtime = "nodejs";
// A real HR answer chains two local (CPU, Ollama) LLM calls and can take
// 60-90s+ on a fresh (uncached) question - opt into the platform's max
// duration rather than the low default. Still not always enough; the proxy
// below falls back gracefully rather than erroring if this is exceeded.
export const maxDuration = 60;

const audit = (
  node: string,
  decision: string,
  source: string,
  request_id: string,
): AuditTrailEntry => ({ node, decision, source, request_id });

function backendResponse(rawInput: string, requestId: string): AgentResponse {
  const raw = rawInput ?? "";
  const valid = typeof raw === "string" && raw.trim().length > 0 && raw.length <= 8000;
  const text = valid ? scrubPii(raw.trim()) : "";
  const injection = INJECTION_RE.test(text);
  const security = SECURITY_RE.test(text);

  let decision: string;
  let reason: string | null;
  let triage: AgentResponse["backend_triage"];
  let confidence = 0;

  if (!valid || injection) {
    triage = { status: "blocked", accepted: false, advisory_only: true };
    reason = !valid ? "invalid_ticket_input" : "security_review";
    decision = "human_escalation";
  } else {
    const p = predictHelpdesk(text);
    confidence = p.confidence;
    triage = {
      status: p.status,
      model_type: p.model_type,
      model_version: p.model_version,
      category: p.category,
      suggested_team: p.suggested_team,
      confidence: p.confidence,
      scores: p.scores,
      threshold: p.threshold,
      accepted: p.accepted,
      advisory_only: true,
      production_validated: p.production_validated,
    };

    if (security || (p.accepted && p.category === "Security")) {
      reason = "security_incident_review";
      triage.review_team = "Security Team";
      decision = "human_escalation";
    } else if (p.accepted && p.category === "Access") {
      reason = "access_authorization_required";
      decision = "human_escalation";
    } else if (!p.accepted) {
      reason = p.status === "classified" ? "it_classification_uncertain" : "it_classifier_" + p.status;
      decision = "human_escalation";
    } else if (p.confidence < ROUTE_MARGIN) {
      reason = "it_classification_low_margin";
      decision = "human_escalation";
    } else {
      reason = null;
      decision = "route_to_team:" + p.suggested_team;
    }
  }

  const routed = decision.startsWith("route_to_team:");
  const cat = triage?.category;
  let rationale: string;
  let evidence_ids: string[];
  if (routed && triage) {
    rationale =
      `TF-IDF + logistic-regression IT classifier: ${cat} @ score ` +
      `${(triage.confidence ?? 0).toFixed(2)} (accept gate ${(triage.threshold ?? 0.5).toFixed(2)}); ` +
      `routing to ${triage.suggested_team}. Classification only — the assigned team resolves the ticket.`;
    evidence_ids = [
      `tfidf:${triage.model_version}`,
      `it_category:${cat}`,
      `suggested_team:${triage.suggested_team}`,
    ];
  } else {
    rationale = "Backend specialist review required.";
    if (cat) {
      rationale =
        `IT classifier suggests ${cat} / ${triage?.suggested_team} @ score ` +
        `${(triage?.confidence ?? 0).toFixed(2)}. Human review required (${reason}); ` +
        `classification does not resolve the ticket.`;
    }
    if (triage?.review_team) rationale += " Security policy requires Security Team review.";
    evidence_ids = [];
  }

  const trail: AuditTrailEntry[] = [
    audit("orchestrator", "backend_ticketing", "local_model", requestId),
    audit("backend_head", decision, "local_model", requestId),
    audit("verifier", "pass", "local_model", requestId),
  ];
  trail[1] = {
    ...trail[1],
    ...(triage?.model_version ? { model_version: triage.model_version } : {}),
    ...(cat ? { it_category: cat } : {}),
    review_reason: reason,
  } as AuditTrailEntry;

  return {
    decision,
    confidence,
    rationale,
    evidence_ids,
    source: "local_model",
    audit_trail: trail,
    department_history: null,
    backend_triage: triage ?? null,
    escalation_reason: reason,
    needs_human: decision === "human_escalation" || Boolean(reason),
  };
}

function otherDepartmentResponse(
  dept: Department,
  requestId: string,
): AgentResponse {
  const label = DEPT_LABEL[dept];
  const node = DEPT_NODE[dept];
  return {
    decision: "human_escalation",
    confidence: 0,
    rationale:
      `Orchestrator routed this to ${label}. The ${label} specialist backend ` +
      `(credit model / legal RAG / CrewAI review) runs as its own service and is ` +
      `not bundled into this deployment — the ticket is queued for a human reviewer.`,
    evidence_ids: [],
    source: "human_escalation",
    audit_trail: [
      audit("orchestrator", dept, "local_model", requestId),
      audit(node, "human_escalation", "human_escalation", requestId),
      audit("verifier", "pass", "local_model", requestId),
    ],
    department_history: null,
    backend_triage: null,
    escalation_reason: null,
    needs_human: true,
  };
}

export async function POST(req: NextRequest) {
  let body: { request_id?: string; raw_input?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }
  const requestId = body.request_id || "req-" + Math.random().toString(36).slice(2, 8);
  const rawInput = (body.raw_input ?? "").toString();

  if (!rawInput.trim()) {
    return NextResponse.json({ detail: "raw_input is required" }, { status: 400 });
  }

  // When set, prefer the real Python backend (full orchestrator + real
  // department logic, e.g. hr-agent, NiryatAI, legal RAG - none of which can
  // run in this serverless function) over the in-process reimplementation
  // below. Typically a tunnel URL for a backend running outside Vercel;
  // falls through to the reimplementation if unset, unreachable, or slow.
  const liveBackendUrl = process.env.AGENT_COMPANY_API_URL;
  if (liveBackendUrl) {
    try {
      const liveResponse = await fetch(
        `${liveBackendUrl.replace(/\/$/, "")}/request`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: requestId, raw_input: rawInput }),
          // Leaves ~5s of the 60s maxDuration budget above for the
          // fallback path itself, in case this is exceeded.
          signal: AbortSignal.timeout(55000),
        },
      );
      if (liveResponse.ok) {
        const result: AgentResponse = await liveResponse.json();
        return NextResponse.json(result, { status: 200 });
      }
      console.error(
        `Live backend at ${liveBackendUrl} returned ${liveResponse.status}; falling back to in-process logic.`,
      );
    } catch (error) {
      console.error(
        `Live backend at ${liveBackendUrl} unreachable; falling back to in-process logic.`,
        error,
      );
    }
  }

  const orch = await localClassify(rawInput);
  // No keyword match -> treat as an IT ticket and let the model + accept gate
  // decide (this mirrors the real orchestrator's LLM fallback, minus the LLM).
  const dept: Department = orch.department ?? "backend_ticketing";

  const result =
    dept === "backend_ticketing"
      ? backendResponse(rawInput, requestId)
      : otherDepartmentResponse(dept, requestId);

  return NextResponse.json(result, { status: 200 });
}
