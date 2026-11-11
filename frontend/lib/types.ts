// Mirrors the Python API (contract.py :: DepartmentResult and api.py).
// Nothing here decides anything; it only describes what the backend returns.

export type Status = "answered" | "needs_human" | "needs_more_info" | "failed";
export type ConfidenceKind = "calibrated_probability" | "retrieval_score" | "none";

export interface Evidence {
  source: string;
  text: string | null;
  data: Record<string, unknown> | null;
}

export interface DepartmentResult {
  department: string;
  status: Status;
  answer: string | null;
  evidence: Evidence[];
  confidence: { kind: ConfidenceKind; value: number | null };
  missing_fields: string[];
  error: string | null;
  reason: string | null;
}

export interface AuditTrailEntry {
  node: string;
  status: string;
  request_id: string;
  department?: string | null;
  reason?: string | null;
  source?: string | null;
  similarity?: number;
  age_seconds?: number;
  note?: string;
  check?: string;
}

export interface AgentResponse {
  request_id: string;
  result: DepartmentResult;
  needs_human: boolean;
  escalation_reason: string | null;
  department_history: { department: string; result: DepartmentResult }[];
  audit_trail: AuditTrailEntry[];
}

// The user types one sentence. They never choose a department.
export interface AgentRequestBody {
  request_id: string;
  raw_input: string;
}
