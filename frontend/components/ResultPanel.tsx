import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentResponse } from "@/lib/types";
import ConfidenceGauge from "./ConfidenceGauge";
import SourceBadge from "./SourceBadge";
import BackendTriage from "./BackendTriage";

export default function ResultPanel({
  result,
  finalDepartmentLabel,
}: {
  result: AgentResponse;
  finalDepartmentLabel?: string;
}) {
  return (
    <div data-testid="result-panel" className="glass-panel rounded-lg">
      <div className="flex items-center justify-between border-b px-4 py-2.5" style={{ borderColor: "var(--border-hairline)" }}>
        <span className="font-mono text-[11px] uppercase tracking-widest" style={{ color: "var(--text-secondary)" }}>
          {finalDepartmentLabel ? `final result — ${finalDepartmentLabel}` : "result"}
        </span>
        <SourceBadge source={result.source} />
      </div>

      <div className="flex flex-col gap-5 p-5">
       <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
        <div className="flex shrink-0 justify-center sm:justify-start">
          <ConfidenceGauge confidence={result.confidence} />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
              decision
            </div>
            <div data-testid="result-decision" className="text-xl font-semibold" style={{ color: "var(--text-primary)" }}>
              {result.decision}
            </div>
          </div>

          {result.rationale && (
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
                rationale
              </div>
              <div className="prose-invert max-w-none text-[13px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.rationale}</ReactMarkdown>
              </div>
            </div>
          )}

          {result.evidence_ids.length > 0 && (
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
                evidence
              </div>
              <div className="flex flex-wrap gap-1.5">
                {result.evidence_ids.map((id) => (
                  <span
                    key={id}
                    className="rounded border px-1.5 py-0.5 font-mono text-[10px]"
                    style={{ borderColor: "var(--border-hairline-strong)", color: "var(--text-secondary)" }}
                  >
                    {id}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
       </div>

        {result.backend_triage && (
          <BackendTriage
            triage={result.backend_triage}
            decision={result.decision}
            escalationReason={result.escalation_reason}
          />
        )}
      </div>
    </div>
  );
}
