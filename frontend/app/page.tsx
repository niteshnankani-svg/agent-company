"use client";

import { useEffect, useState } from "react";
import { SCENARIOS } from "@/lib/scenarios";
import type { AgentRequestBody, AgentResponse } from "@/lib/types";
import { API_BASE_URL, ApiError, checkHealth, sendAgentRequest } from "@/lib/api";
import { DEPT_ACCENT_COLOR } from "@/lib/accents";
import ScenarioForm from "@/components/ScenarioForm";
import TraceGraph from "@/components/TraceGraph";
import ResultPanel from "@/components/ResultPanel";
import SequentialPipeline from "@/components/SequentialPipeline";
import AskConsole from "@/components/AskConsole";

type ViewMode = "ask" | "advanced";

function AdvancedConsole() {
  const [scenarioId, setScenarioId] = useState(SCENARIOS[0].id);
  const scenario = SCENARIOS.find((s) => s.id === scenarioId)!;

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AgentResponse | null>(null);

  async function handleSubmit(body: AgentRequestBody) {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await sendAgentRequest(body);
      setResult(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Unexpected error contacting the backend.");
    } finally {
      setLoading(false);
    }
  }

  const finalDeptLabel =
    scenario.kind === "sequential" && result?.department_history
      ? scenario.sequence[scenario.sequence.length - 1]?.label
      : undefined;

  return (
    <>
      <div className="flex flex-1 flex-col gap-2 border-b px-6 py-3 sm:flex-row sm:items-center" style={{ borderColor: "var(--border-hairline)" }}>
        {SCENARIOS.map((s) => {
          const accentColor = s.kind === "single" ? DEPT_ACCENT_COLOR[s.accent] : "var(--source-local)";
          const active = s.id === scenarioId;
          return (
            <button
              key={s.id}
              data-testid={`scenario-tab-${s.id}`}
              onClick={() => {
                setScenarioId(s.id);
                setResult(null);
                setError(null);
              }}
              className="rounded-md border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors"
              style={{
                borderColor: active ? accentColor : "var(--border-hairline-strong)",
                color: active ? accentColor : "var(--text-secondary)",
                background: active ? `color-mix(in srgb, ${accentColor} 10%, transparent)` : "transparent",
              }}
            >
              {s.name}
            </button>
          );
        })}
      </div>

      <main className="grid flex-1 grid-cols-1 gap-6 p-6 lg:grid-cols-[360px_1fr]">
        <div className="glass-panel rounded-lg p-4">
          <ScenarioForm key={scenario.id} scenario={scenario} onSubmit={handleSubmit} loading={loading} />
        </div>

        <div className="flex min-w-0 flex-col gap-6">
          <TraceGraph trail={result?.audit_trail ?? []} />

          {error && (
            <div
              data-testid="request-error"
              className="rounded-lg border px-4 py-3 font-mono text-[12px]"
              style={{ borderColor: "var(--source-human)", color: "var(--source-human)", background: "color-mix(in srgb, var(--source-human) 8%, transparent)" }}
            >
              {error}
            </div>
          )}

          {result?.department_history && result.department_history.length > 0 && (
            <SequentialPipeline history={result.department_history} />
          )}

          {result && <ResultPanel result={result} finalDepartmentLabel={finalDeptLabel} />}
        </div>
      </main>
    </>
  );
}

export default function Home() {
  const [view, setView] = useState<ViewMode>("ask");
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const ok = await checkHealth();
      if (!cancelled) setHealthy(ok);
    }
    poll();
    const id = setInterval(poll, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b px-6 py-4" style={{ borderColor: "var(--border-hairline)", background: "color-mix(in srgb, var(--bg-deep) 88%, transparent)" }}>
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded border font-mono text-xs" style={{ borderColor: "var(--source-local)", color: "var(--source-local)" }}>
            AC
          </div>
          <div>
            <div className="font-mono text-sm font-semibold tracking-wide" style={{ color: "var(--text-primary)" }}>
              AGENT COMPANY
            </div>
            <div className="font-mono text-[10px] uppercase tracking-widest" style={{ color: "var(--text-tertiary)" }}>
              graph traversal console
            </div>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1 rounded-md border p-0.5" style={{ borderColor: "var(--border-hairline-strong)" }}>
            {(["ask", "advanced"] as ViewMode[]).map((m) => (
              <button
                key={m}
                data-testid={`view-toggle-${m}`}
                onClick={() => setView(m)}
                className="rounded px-3 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors"
                style={{
                  background: view === m ? "color-mix(in srgb, var(--source-local) 14%, transparent)" : "transparent",
                  color: view === m ? "var(--source-local)" : "var(--text-tertiary)",
                }}
              >
                {m}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 font-mono text-[11px]" style={{ color: "var(--text-secondary)" }} data-testid="health-indicator">
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: healthy === null ? "var(--text-tertiary)" : healthy ? "var(--source-cache)" : "var(--source-human)" }}
            />
            {API_BASE_URL}
            <span style={{ color: "var(--text-tertiary)" }}>
              {healthy === null ? "· checking" : healthy ? "· online" : "· unreachable"}
            </span>
          </div>
        </div>
      </header>

      {view === "ask" ? (
        <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 p-6">
          <AskConsole />
        </main>
      ) : (
        <AdvancedConsole />
      )}
    </div>
  );
}
