"use client";

import { useState } from "react";
import type { Scenario, FieldSpec } from "@/lib/scenarios";
import { coerceFieldValue } from "@/lib/scenarios";
import { generateRequestId } from "@/lib/api";
import type { AgentRequestBody } from "@/lib/types";
import { DEPT_ACCENT_COLOR } from "@/lib/accents";

function Field({
  field,
  value,
  onChange,
}: {
  field: FieldSpec;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
        {field.label}
      </span>
      {field.type === "textarea" ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={2}
          className="resize-none rounded border bg-transparent px-2.5 py-1.5 font-mono text-[12.5px] outline-none transition-colors focus:border-[var(--accent-export)]"
          style={{ borderColor: "var(--border-hairline-strong)", color: "var(--text-primary)" }}
        />
      ) : (
        <input
          type={field.type === "number" ? "number" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="rounded border bg-transparent px-2.5 py-1.5 font-mono text-[12.5px] outline-none transition-colors focus:border-[var(--accent-export)]"
          style={{ borderColor: "var(--border-hairline-strong)", color: "var(--text-primary)" }}
        />
      )}
    </label>
  );
}

export default function ScenarioForm({
  scenario,
  onSubmit,
  loading,
}: {
  scenario: Scenario;
  onSubmit: (body: AgentRequestBody) => void;
  loading: boolean;
}) {
  const [rawInput, setRawInput] = useState(scenario.rawInputDefault);

  const [singleValues, setSingleValues] = useState<Record<string, string>>(() =>
    scenario.kind === "single"
      ? Object.fromEntries(scenario.fields.map((f) => [f.key, String(f.defaultValue)]))
      : {},
  );

  const [stepValues, setStepValues] = useState<Record<string, Record<string, string>>>(() =>
    scenario.kind === "sequential"
      ? Object.fromEntries(
          scenario.sequence.map((step) => [
            step.department,
            Object.fromEntries(step.fields.map((f) => [f.key, String(f.defaultValue)])),
          ]),
        )
      : {},
  );

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (scenario.kind === "single") {
      const department_fields = Object.fromEntries(
        scenario.fields.map((f) => [f.key, coerceFieldValue(f, singleValues[f.key] ?? "")]),
      );
      onSubmit({
        request_id: generateRequestId(scenario.id),
        raw_input: rawInput,
        department_fields,
        sub_department: scenario.subDepartment,
      });
    } else {
      const department_fields_by_dept: Record<string, { fields: Record<string, unknown>; sub_department?: string }> = {};
      scenario.sequence.forEach((step) => {
        const fields = Object.fromEntries(
          step.fields.map((f) => [f.key, coerceFieldValue(f, stepValues[step.department]?.[f.key] ?? "")]),
        );
        department_fields_by_dept[step.department] = {
          fields,
          ...(step.subDepartment ? { sub_department: step.subDepartment } : {}),
        };
      });
      onSubmit({
        request_id: generateRequestId(scenario.id),
        raw_input: rawInput,
        department_sequence: scenario.sequence.map((s) => s.department),
        department_fields_by_dept,
      });
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
          {scenario.tagline}
        </p>
      </div>

      <Field
        field={{ key: "raw_input", label: "raw_input", type: "textarea", defaultValue: "" }}
        value={rawInput}
        onChange={setRawInput}
      />

      {scenario.kind === "single" && scenario.samples && scenario.samples.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {scenario.samples.map((s) => {
            const active = rawInput === s.text;
            const guard = s.label.startsWith("⚠");
            const tint = guard ? "var(--source-human)" : DEPT_ACCENT_COLOR[scenario.accent];
            return (
              <button
                key={s.label}
                type="button"
                onClick={() => setRawInput(s.text)}
                aria-pressed={active}
                className="rounded-full border px-2.5 py-1 font-mono text-[10.5px] transition-colors"
                style={{
                  borderColor: active ? tint : "var(--border-hairline-strong)",
                  color: active || guard ? tint : "var(--text-secondary)",
                  background: active ? `color-mix(in srgb, ${tint} 12%, transparent)` : "transparent",
                }}
              >
                {s.label}
              </button>
            );
          })}
        </div>
      )}

      {scenario.kind === "single" && scenario.fields.length > 0 && (
        <div className="grid grid-cols-2 gap-3">
          {scenario.fields.map((f) => (
            <Field key={f.key} field={f} value={singleValues[f.key] ?? ""} onChange={(v) => setSingleValues((s) => ({ ...s, [f.key]: v }))} />
          ))}
        </div>
      )}

      {scenario.kind === "sequential" &&
        scenario.sequence.map((step, idx) => (
          <div key={step.department} className="rounded-md border p-3" style={{ borderColor: "var(--border-hairline)" }}>
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-4 w-4 items-center justify-center rounded-full font-mono text-[9px]" style={{ background: DEPT_ACCENT_COLOR[step.accent], color: "#06080c" }}>
                {idx + 1}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: DEPT_ACCENT_COLOR[step.accent] }}>
                {step.label}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {step.fields.map((f) => (
                <Field
                  key={f.key}
                  field={f}
                  value={stepValues[step.department]?.[f.key] ?? ""}
                  onChange={(v) =>
                    setStepValues((s) => ({ ...s, [step.department]: { ...s[step.department], [f.key]: v } }))
                  }
                />
              ))}
            </div>
          </div>
        ))}

      <button
        type="submit"
        data-testid="run-request-button"
        disabled={loading}
        className="mt-1 rounded-md border px-4 py-2.5 font-mono text-[12px] uppercase tracking-wider transition-colors disabled:opacity-40"
        style={{
          borderColor: "var(--source-local)",
          color: loading ? "var(--text-tertiary)" : "var(--source-local)",
          background: "color-mix(in srgb, var(--source-local) 8%, transparent)",
        }}
      >
        {loading ? "dispatching to graph…" : "▶ run request"}
      </button>
    </form>
  );
}
