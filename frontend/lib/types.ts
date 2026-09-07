export type SourceType = "local_model" | "llm" | "cache" | "human_escalation";

export interface DepartmentOutput {
  decision: string;
  confidence: number;
  rationale: string;
  evidence_ids: string[];
  source: SourceType;
}

export interface AuditTrailEntry {
  node: string;
  decision: string;
  source: string;
  request_id: string;
}

export interface DepartmentHistoryEntry {
  department: string;
  output: DepartmentOutput;
}

// Backend / Ticketing is one department in the graph. When the orchestrator
// routes there, backend_head_node attaches the DistilBERT triage it ran
// (backend_ticketing.py -> backend_model.predict_backend). Every field is
// optional because a blocked / disabled / unavailable model returns only a
// status, and an older backend build omits the key entirely.
export interface BackendTriage {
  status?: string;
  model_type?: string;
  model_version?: string;
  category?: string | null;
  suggested_team?: string | null;
  confidence?: number;
  scores?: Record<string, number>;
  threshold?: number;
  accepted?: boolean;
  review_team?: string;
  advisory_only?: boolean;
  production_validated?: boolean;
  truncated?: boolean;
}

export interface AgentResponse {
  decision: string;
  confidence: number;
  rationale: string;
  evidence_ids: string[];
  source: SourceType;
  audit_trail: AuditTrailEntry[];
  department_history: DepartmentHistoryEntry[] | null;
  // Present on every response from api.py; may be null / absent on older builds.
  backend_triage?: BackendTriage | null;
  escalation_reason?: string | null;
  needs_human?: boolean;
}

export interface SingleDepartmentRequestBody {
  request_id: string;
  raw_input: string;
  department_fields: Record<string, unknown>;
  sub_department?: string | null;
}

export interface SequentialRequestBody {
  request_id: string;
  raw_input: string;
  department_sequence: string[];
  department_fields_by_dept: Record<
    string,
    { fields: Record<string, unknown>; sub_department?: string }
  >;
}

export type AgentRequestBody = SingleDepartmentRequestBody | SequentialRequestBody;
