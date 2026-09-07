import type { AgentRequestBody, AgentResponse } from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";

export class ApiError extends Error {
  status?: number;
  // Raw `detail` string from the backend's JSON error body, when present.
  // api.py wraps every uncaught exception in {"detail": "Request processing
  // failed: <str(exception)>"} — this is that raw Python exception text, not
  // a stable API contract, but it's the only signal available today for
  // distinguishing *why* a request failed.
  detail?: string;
  constructor(message: string, status?: number, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// Requests go through same-origin Next.js API routes (app/api/agent/*), which
// proxy to API_BASE_URL server-side. The real backend does not send CORS
// headers, so a direct browser -> backend fetch is blocked; the proxy avoids
// that without changing the request/response contract in any way.
export async function sendAgentRequest(
  body: AgentRequestBody,
): Promise<AgentResponse> {
  let res: Response;
  try {
    res = await fetch("/api/agent/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      `Could not reach the Agent Company API at ${API_BASE_URL}. Is the backend running and NEXT_PUBLIC_API_BASE_URL set correctly?`,
    );
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail: string | undefined;
    try {
      detail = JSON.parse(text)?.detail;
    } catch {
      // body wasn't JSON — leave detail undefined, fall back to raw text below
    }
    throw new ApiError(
      `Request failed (${res.status} ${res.statusText}): ${detail || text || "no detail returned"}`,
      res.status,
      detail,
    );
  }

  return (await res.json()) as AgentResponse;
}

// Finance's department head raises a plain ValueError when sub_department
// isn't "ar"/"ap" (finance.py:170), which api.py turns into an uncaught 500 —
// never a graceful `human_escalation` response. This is the only observable
// signal that a request was routed to Finance without the structured fields
// it needs. Matching backend exception text is inherently fragile; the
// correct long-term fix is a backend change to catch this and return a
// normal DepartmentOutput instead of raising.
export function isFinanceNeedsFieldsError(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 500) return false;
  const text = (err.detail || err.message).toLowerCase();
  return text.includes("finance sub-department") || text.includes("finance sub_department");
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch("/api/agent/health", { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

export function generateRequestId(prefix: string): string {
  const rand = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${rand}`;
}
