"use client";

import { motion } from "framer-motion";
import type { BackendTriage as Triage } from "@/lib/types";

// The five labels the DistilBERT head is trained on (backend_ticketing.py).
const IT_CLASSES = ["Access", "Hardware", "Network", "Security", "Software"] as const;

const REASON_TEXT: Record<string, string> = {
  security_incident_review:
    "Security signal or Security class — policy routes this to the Security Team, never auto-assigned.",
  access_authorization_required:
    "Access-control request — a human must authorize before anything changes.",
  it_classification_uncertain:
    "Top class is below the model's accept gate — too uncertain to act on.",
  it_classification_low_margin:
    "Cleared the accept gate but under 0.65 — margin too thin to auto-route.",
  it_classifier_unavailable:
    "The DistilBERT worker could not be reached — failing closed to a human.",
  it_classifier_disabled: "The model is switched off (BACKEND_MODEL_ENABLED=0).",
  security_review:
    "Input matched a prompt-injection pattern — blocked before the model ran.",
  invalid_ticket_input: "Empty, oversized, or malformed ticket text.",
  backend_resolution_review_required: "Routed for specialist review.",
};

const ACCENT = "var(--accent-backend)";
const GO = "var(--source-cache)";
const HOLD = "var(--source-human)";
const WAIT = "var(--source-llm)";

function pct(n: number | undefined): string {
  return `${((n ?? 0) * 100).toFixed(1)}%`;
}

export default function BackendTriage({
  triage,
  decision,
  escalationReason,
}: {
  triage: Triage;
  decision: string;
  escalationReason?: string | null;
}) {
  const classified = triage.status === "classified" && !!triage.scores;
  const modelName =
    triage.model_type === "distilbert"
      ? "DistilBERT"
      : triage.model_type === "tfidf"
        ? "TF-IDF"
        : "model";
  const routed = decision.startsWith("route_to_team:");
  // On an escalation, only name a team if policy forced one (review_team);
  // the model's suggested_team is shown separately and would mislead here.
  const team = routed
    ? decision.slice("route_to_team:".length)
    : triage.review_team ?? null;
  const gate = triage.threshold ?? 0.55;

  return (
    <div className="rounded-lg border" style={{ borderColor: "var(--border-hairline)" }}>
      <div
        className="flex items-center justify-between border-b px-4 py-2.5"
        style={{ borderColor: "var(--border-hairline)" }}
      >
        <span
          className="font-mono text-[11px] uppercase tracking-widest"
          style={{ color: ACCENT }}
        >
          {modelName.toLowerCase()} triage · backend / ticketing
        </span>
        <span
          className="font-mono text-[10px]"
          style={{ color: "var(--text-tertiary)" }}
        >
          {triage.model_version ?? triage.model_type ?? "model"}
        </span>
      </div>

      <div className="flex flex-col gap-4 p-4">
        {classified ? (
          <>
            <div className="flex flex-col gap-2">
              {[...IT_CLASSES]
                .sort((a, b) => (triage.scores![b] ?? 0) - (triage.scores![a] ?? 0))
                .map((c) => {
                  const v = triage.scores![c] ?? 0;
                  const win = c === triage.category;
                  return (
                    <div key={c} className="grid grid-cols-[84px_1fr_52px] items-center gap-3">
                      <span
                        className="font-mono text-[11px]"
                        style={{ color: win ? "var(--text-primary)" : "var(--text-secondary)" }}
                      >
                        {c}
                      </span>
                      <span
                        className="h-[7px] overflow-hidden rounded-full"
                        style={{ background: "var(--bg-inset)", border: "1px solid var(--border-hairline)" }}
                      >
                        <motion.span
                          className="block h-full rounded-full"
                          style={{ background: win ? ACCENT : "var(--border-hairline-strong)" }}
                          initial={{ width: 0 }}
                          animate={{ width: `${(v * 100).toFixed(1)}%` }}
                          transition={{ duration: 0.5, ease: "easeOut" }}
                        />
                      </span>
                      <span
                        className="mono-tabular text-right text-[11px]"
                        style={{ color: win ? "var(--text-primary)" : "var(--text-tertiary)" }}
                      >
                        {pct(v)}
                      </span>
                    </div>
                  );
                })}
            </div>

            {/* accept gate */}
            <div className="flex items-center gap-3 font-mono text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              <span>0</span>
              <span className="relative h-[6px] flex-1 rounded-full" style={{ background: "var(--bg-inset)", border: "1px solid var(--border-hairline)" }}>
                <span
                  className="absolute -top-[4px] h-[14px] w-[2px]"
                  style={{ left: `${gate * 100}%`, background: "var(--text-secondary)" }}
                />
                <span
                  className="absolute top-1/2 h-[10px] w-[10px] -translate-x-1/2 -translate-y-1/2 rounded-full"
                  style={{
                    left: `${(triage.confidence ?? 0) * 100}%`,
                    background: triage.accepted ? GO : HOLD,
                    border: "2px solid var(--bg-panel)",
                  }}
                />
              </span>
              <span>gate {gate.toFixed(2)}</span>
            </div>

            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[11.5px]">
              <dt style={{ color: "var(--text-tertiary)" }}>top class</dt>
              <dd style={{ color: "var(--text-primary)" }}>
                {triage.category} @ {pct(triage.confidence)}{" "}
                <span style={{ color: triage.accepted ? GO : HOLD }}>
                  {triage.accepted ? "✓ cleared" : "✗ below gate"}
                </span>
              </dd>
              <dt style={{ color: "var(--text-tertiary)" }}>suggested team</dt>
              <dd style={{ color: "var(--text-secondary)" }}>{triage.suggested_team ?? "—"}</dd>
            </dl>
          </>
        ) : (
          <p className="text-[12.5px]" style={{ color: "var(--text-secondary)" }}>
            Model did not classify this ticket —{" "}
            <span className="font-mono" style={{ color: HOLD }}>
              status: {triage.status ?? "unknown"}
            </span>
            . PII scrubbing and the prompt-injection / security guard run{" "}
            <em>before</em> the model, so blocked input never reaches the classifier.
          </p>
        )}

        {/* decision strip */}
        <div
          className="flex flex-wrap items-center gap-2 border-t pt-3"
          style={{ borderColor: "var(--border-hairline)" }}
        >
          <span
            className="rounded-full border px-2.5 py-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-wide"
            style={{
              color: routed ? GO : HOLD,
              borderColor: `color-mix(in srgb, ${routed ? GO : HOLD} 45%, transparent)`,
              background: `color-mix(in srgb, ${routed ? GO : HOLD} 12%, transparent)`,
            }}
          >
            {routed ? "route_to_team" : "human_escalation"}
          </span>
          {team && (
            <span
              className="rounded-full border px-2.5 py-0.5 font-mono text-[10.5px] font-semibold"
              style={{
                color: routed ? ACCENT : WAIT,
                borderColor: `color-mix(in srgb, ${routed ? ACCENT : WAIT} 45%, transparent)`,
                background: `color-mix(in srgb, ${routed ? ACCENT : WAIT} 12%, transparent)`,
              }}
            >
              {team}
            </span>
          )}
          {!routed && escalationReason && (
            <span className="font-mono text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              {escalationReason}
            </span>
          )}
        </div>

        <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          {routed
            ? `Classification only — the ${team} queue still works the ticket. The model never marks a ticket resolved.`
            : REASON_TEXT[escalationReason ?? ""] ??
              "Handed to a human reviewer; the model's suggestion is attached for context."}
        </p>
      </div>
    </div>
  );
}
