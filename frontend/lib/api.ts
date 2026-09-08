import type { AgentRequestBody, AgentResponse } from "./types";

// The orchestrator + Backend/Ticketing IT-triage model run inside this
// deployment's own serverless routes (app/api/agent/*). Nothing external to
// reach — this label is just what the header shows.
export const API_BASE_URL = "in-process · tfidf-logistic";

export class ApiError extends Error {
  status?: number;
  detail?: string;
  constructor(message: string, status?: number, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

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
    throw new ApiError("Could not reach the agent route. Try again.");
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail: string | undefined;
    try {
      detail = JSON.parse(text)?.detail;
    } catch {
      // not JSON
    }
    throw new ApiError(
      `Request failed (${res.status}): ${detail || text || "no detail"}`,
      res.status,
      detail,
    );
  }

  return (await res.json()) as AgentResponse;
}

// Kept for AskConsole's import; the in-process route never raises this, so the
// Finance follow-up form path is simply never triggered.
export function isFinanceNeedsFieldsError(_err: unknown): boolean {
  return false;
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
