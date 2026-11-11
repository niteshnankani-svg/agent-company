"use client";

import { useState } from "react";
import type { FieldSpec, FollowUp } from "@/lib/types";

// Renders exactly the questions the backend asked for. It knows nothing about finance
// or any other department: labels, kinds, choices and problems all come from the API.

function toValue(spec: FieldSpec, raw: string): unknown {
  if (raw === "") return null; // blank = not answered
  if (spec.kind === "boolean") return raw === "yes";
  if (spec.kind === "number" || spec.kind === "integer") {
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n; // let the backend explain what is wrong with it
  }
  return raw;
}

export default function FollowUpForm({
  followUp,
  reason,
  loading,
  onSubmit,
}: {
  followUp: FollowUp;
  reason: string | null;
  loading: boolean;
  onSubmit: (answers: Record<string, unknown>) => void;
}) {
  const [raw, setRaw] = useState<Record<string, string>>({});

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const answers: Record<string, unknown> = {};
    for (const spec of followUp.fields) {
      const value = toValue(spec, raw[spec.name] ?? "");
      if (value !== null) answers[spec.name] = value;
    }
    onSubmit(answers);
  }

  const radio = (spec: FieldSpec, value: string, label: string) => (
    <label key={value} className="flex items-center gap-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>
      <input
        type="radio"
        name={spec.name}
        value={value}
        checked={raw[spec.name] === value}
        onChange={() => setRaw((r) => ({ ...r, [spec.name]: value }))}
      />
      {label}
    </label>
  );

  return (
    <form onSubmit={submit} data-testid="follow-up-form" className="glass-panel rounded-xl p-5">
      <p className="mb-1 text-[14px]" style={{ color: "var(--text-primary)" }}>
        A few more details are needed to continue.
      </p>
      {reason === "extraction_unavailable" && (
        <p className="mb-3 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
          Your message could not be read automatically right now, so please fill these in.
        </p>
      )}
      <div className="mt-3 flex flex-col gap-4">
        {followUp.fields.map((spec) => (
          <div key={spec.name} data-testid={`field-${spec.name}`} className="flex flex-col gap-1.5">
            <label htmlFor={`f-${spec.name}`} className="text-[13px]" style={{ color: "var(--text-primary)" }}>
              {spec.label}
            </label>
            {spec.help && (
              <span className="text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>
                {spec.help}
              </span>
            )}
            {spec.kind === "boolean" && (
              <div className="flex gap-5">
                {radio(spec, "yes", "Yes")}
                {radio(spec, "no", "No")}
              </div>
            )}
            {spec.kind === "choice" && (
              <div className="flex flex-col gap-1.5">
                {Object.entries(spec.choices ?? {}).map(([value, label]) => radio(spec, value, label))}
              </div>
            )}
            {(spec.kind === "number" || spec.kind === "integer" || spec.kind === "text") && (
              <input
                id={`f-${spec.name}`}
                type={spec.kind === "text" ? "text" : "number"}
                inputMode={spec.kind === "integer" ? "numeric" : "decimal"}
                step={spec.kind === "integer" ? 1 : "any"}
                value={raw[spec.name] ?? ""}
                onChange={(e) => setRaw((r) => ({ ...r, [spec.name]: e.target.value }))}
                className="rounded border bg-transparent px-2.5 py-1.5 font-mono text-[13px] outline-none transition-colors focus:border-[var(--source-local)]"
                style={{ borderColor: spec.problem ? "var(--source-human)" : "var(--border-hairline-strong)", color: "var(--text-primary)" }}
              />
            )}
            {spec.problem && (
              <span role="alert" className="text-[12px]" style={{ color: "var(--source-human)" }}>
                {spec.problem}
              </span>
            )}
          </div>
        ))}
      </div>
      <button
        type="submit"
        data-testid="follow-up-submit"
        disabled={loading}
        className="mt-5 rounded-md border px-4 py-2 font-mono text-[12px] uppercase tracking-wider transition-colors disabled:opacity-40"
        style={{
          borderColor: "var(--source-local)",
          color: "var(--source-local)",
          background: "color-mix(in srgb, var(--source-local) 10%, transparent)",
        }}
      >
        {loading ? "working…" : "▶ continue"}
      </button>
    </form>
  );
}
