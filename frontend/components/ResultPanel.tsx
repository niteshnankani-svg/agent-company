import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentResponse, Evidence } from "@/lib/types";
import { reasonText, statusMeta } from "@/lib/accents";

const LABEL = "mb-1 font-mono text-[10px] uppercase tracking-wider";

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className={LABEL} style={{ color: "var(--text-tertiary)" }}>
      {children}
    </div>
  );
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-invert max-w-none text-[13.5px] leading-relaxed" style={{ color: "var(--text-primary)" }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function EvidenceItem({ item }: { item: Evidence }) {
  return (
    <details className="rounded border px-3 py-2" style={{ borderColor: "var(--border-hairline-strong)" }}>
      <summary className="cursor-pointer font-mono text-[11px]" style={{ color: "var(--text-secondary)" }}>
        {item.source}
      </summary>
      {item.text && (
        <p className="mt-2 whitespace-pre-wrap text-[12.5px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {item.text}
        </p>
      )}
      {item.data && (
        <pre className="mt-2 overflow-x-auto font-mono text-[11px]" style={{ color: "var(--text-tertiary)" }}>
          {JSON.stringify(item.data, null, 2)}
        </pre>
      )}
    </details>
  );
}

const CONFIDENCE_TEXT: Record<string, string> = {
  retrieval_score: "How closely the question matches the retrieved text. This is not the chance the answer is right.",
  calibrated_probability: "A measured probability that the answer is right.",
};

export default function ResultPanel({ response }: { response: AgentResponse }) {
  const { result } = response;
  const meta = statusMeta(result.status);
  const reason = reasonText(result.reason);
  const cacheHit = response.audit_trail.find((e) => e.node === "cache_lookup" && e.status === "hit");

  return (
    <div data-testid="result-panel" className="glass-panel rounded-lg">
      <div className="flex items-center justify-between border-b px-4 py-2.5" style={{ borderColor: "var(--border-hairline)" }}>
        <span className="font-mono text-[11px] uppercase tracking-widest" style={{ color: "var(--text-secondary)" }}>
          result · handled by {result.department.replaceAll("_", " ")}
        </span>
        <span
          data-testid="result-status"
          data-status={result.status}
          title={meta.description}
          className="rounded-full border px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider"
          style={{ borderColor: meta.color, color: meta.color, background: `color-mix(in srgb, ${meta.color} 10%, transparent)` }}
        >
          {meta.label}
        </span>
      </div>

      <div className="flex flex-col gap-5 p-5">
        {result.status === "answered" && result.answer && (
          <div data-testid="result-answer">
            <Markdown text={result.answer} />
          </div>
        )}

        {result.status === "needs_human" && (
          <>
            <div data-testid="result-needs-human" className="text-[14px]" style={{ color: "var(--text-primary)" }}>
              This has been passed to a person for review.
              {reason && <span style={{ color: "var(--text-secondary)" }}> {reason}</span>}
            </div>
            {result.answer && (
              <div>
                <Label>draft for the reviewer (not verified)</Label>
                <Markdown text={result.answer} />
              </div>
            )}
          </>
        )}

        {result.status === "needs_more_info" && (
          <div data-testid="result-needs-more-info">
            <div className="text-[14px]" style={{ color: "var(--text-primary)" }}>
              More information is needed to continue:
            </div>
            <ul className="mt-2 list-disc pl-5 font-mono text-[12.5px]" style={{ color: "var(--text-secondary)" }}>
              {result.missing_fields.map((f) => (
                <li key={f}>{f.replaceAll("_", " ")}</li>
              ))}
            </ul>
          </div>
        )}

        {result.status === "failed" && (
          <div data-testid="result-failed" className="text-[14px]" style={{ color: "var(--text-primary)" }}>
            This could not be completed because a service it depends on failed.
            <div className="mt-1 font-mono text-[12px]" style={{ color: "var(--text-secondary)" }}>
              {result.error}
            </div>
          </div>
        )}

        {result.evidence.length > 0 && (
          <div>
            <Label>evidence relied on</Label>
            <div className="flex flex-col gap-1.5">
              {result.evidence.map((item, i) => (
                <EvidenceItem key={`${item.source}-${i}`} item={item} />
              ))}
            </div>
          </div>
        )}

        {result.confidence.kind !== "none" && result.confidence.value !== null && (
          <div data-testid="result-confidence">
            <Label>{result.confidence.kind.replaceAll("_", " ")}</Label>
            <div className="font-mono text-[13px]" style={{ color: "var(--text-primary)" }}>
              {result.confidence.value.toFixed(2)}
            </div>
            <div className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>
              {CONFIDENCE_TEXT[result.confidence.kind]}
            </div>
          </div>
        )}

        {cacheHit && (
          <div className="font-mono text-[11px]" style={{ color: "var(--text-tertiary)" }}>
            Reused a stored answer (similarity {cacheHit.similarity}).
          </div>
        )}
      </div>
    </div>
  );
}
