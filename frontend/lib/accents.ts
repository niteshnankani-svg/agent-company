export type DeptAccent = "finance" | "export" | "legal" | "backend_ticketing";

export const DEPT_ACCENT_COLOR: Record<DeptAccent, string> = {
  finance: "var(--accent-finance)",
  export: "var(--accent-export)",
  legal: "var(--accent-legal)",
  backend_ticketing: "var(--accent-backend)",
};

export interface SourceMeta {
  label: string;
  description: string;
  color: string;
}

export const SOURCE_META: Record<string, SourceMeta> = {
  local_model: {
    label: "LOCAL MODEL",
    description: "Fast, cheap, deterministic — resolved on-box",
    color: "var(--source-local)",
  },
  llm: {
    label: "LLM",
    description: "Slower — a real model call happened",
    color: "var(--source-llm)",
  },
  cache: {
    label: "CACHE",
    description: "Instant — reused a prior answer",
    color: "var(--source-cache)",
  },
  human_escalation: {
    label: "HUMAN ESCALATION",
    description: "The system declined to guess",
    color: "var(--source-human)",
  },
};

export function sourceMeta(source: string): SourceMeta {
  return (
    SOURCE_META[source] ?? {
      label: source.toUpperCase(),
      description: "Unknown source",
      color: "var(--text-secondary)",
    }
  );
}
