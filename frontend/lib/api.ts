import type { AgentRequestBody, AgentResponse } from "./types";

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function sendAgentRequest(body: AgentRequestBody): Promise<AgentResponse> {
  let res: Response;
  try {
    res = await fetch("/api/agent/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Could not reach the console server. Try again.");
  }

  if (!res.ok) {
    let detail: string | undefined;
    try {
      detail = (await res.json())?.detail;
    } catch {
      // not JSON
    }
    throw new ApiError(detail || `Request failed (HTTP ${res.status}).`, res.status);
  }
  return (await res.json()) as AgentResponse;
}

export async function fetchRun(runId: string): Promise<AgentResponse> {
  let res: Response;
  try {
    res = await fetch(`/api/agent/runs/${encodeURIComponent(runId)}`, { cache: "no-store" });
  } catch {
    throw new ApiError("Could not reach the console server. Try again.");
  }
  if (!res.ok) {
    let detail: string | undefined;
    try {
      detail = (await res.json())?.detail;
    } catch {
      // not JSON
    }
    throw new ApiError(detail || `Request failed (HTTP ${res.status}).`, res.status);
  }
  return (await res.json()) as AgentResponse;
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
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}
