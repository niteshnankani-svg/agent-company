"use client";

import { useState } from "react";
import type { AgentResponse } from "@/lib/types";
import { ApiError, fetchRun, generateRequestId, sendAgentRequest } from "@/lib/api";
import TraceGraph from "./TraceGraph";
import ResultPanel from "./ResultPanel";
import FollowUpForm from "./FollowUpForm";

type Mode = "idle" | "loading" | "result" | "error";

// Plain example sentences. They are not labelled by team: the user never picks one,
// and nothing here pre-selects where a request goes.
const EXAMPLES = [
  "What is the HS code for cotton t-shirts?",
  "My laptop will not boot, just a black screen after the update",
  "What is the punishment for murder under BNS?",
  "How many employees are in the Sales department?",
];

export default function AskConsole() {
  const [rawInput, setRawInput] = useState("");
  const [mode, setMode] = useState<Mode>("idle");
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The sentence the current answer is about. A follow-up always replays this one, even if the
  // box has been edited since.
  const [submitted, setSubmitted] = useState("");
  const [checking, setChecking] = useState(false);
  const [stillPending, setStillPending] = useState(false);

  // Ask the API whether a person has decided yet on a request that was passed to review.
  async function checkDecision() {
    if (!response) return;
    setChecking(true);
    setError(null);
    try {
      const latest = await fetchRun(response.run_id);
      setResponse(latest);
      setStillPending(latest.pending_review);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setMode("error");
    } finally {
      setChecking(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!rawInput.trim() || mode === "loading") return;
    setMode("loading");
    setError(null);
    setResponse(null);
    setStillPending(false);
    setSubmitted(rawInput);
    try {
      setResponse(await sendAgentRequest({ request_id: generateRequestId("ask"), raw_input: rawInput }));
      setMode("result");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setMode("error");
    }
  }

  // The user answered a form the system generated. Send back what it already understood, merged
  // with the answers, along with the department that form came from (so nothing is routed twice).
  async function submitFollowUp(answers: Record<string, unknown>) {
    const followUp = response?.result.follow_up;
    if (!response || !followUp) return;
    const department = response.result.department;
    setMode("loading");
    setError(null);
    try {
      setResponse(
        await sendAgentRequest({
          request_id: generateRequestId("ask"),
          raw_input: submitted,
          follow_up: {
            department,
            sub_department: followUp.sub_department,
            fields: { ...followUp.collected, ...answers },
          },
        }),
      );
      setMode("result");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setMode("error");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={submit} className="glass-panel glow-ring rounded-xl p-5">
        <label htmlFor="ask-input" className="mb-2 block font-mono text-[11px] uppercase tracking-widest" style={{ color: "var(--text-tertiary)" }}>
          what do you need help with?
        </label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <textarea
            id="ask-input"
            data-testid="ask-input"
            value={rawInput}
            onChange={(e) => setRawInput(e.target.value)}
            rows={3}
            maxLength={8000}
            placeholder="Describe what you need in your own words."
            className="min-w-0 flex-1 resize-none rounded-lg border bg-transparent px-3.5 py-3 text-[14px] outline-none transition-colors focus:border-[var(--source-local)]"
            style={{ borderColor: "var(--border-hairline-strong)", color: "var(--text-primary)" }}
          />
          <button
            type="submit"
            data-testid="ask-submit"
            disabled={mode === "loading" || !rawInput.trim()}
            className="shrink-0 rounded-lg border px-6 py-3 font-mono text-[12px] uppercase tracking-wider transition-colors disabled:opacity-40"
            style={{
              borderColor: "var(--source-local)",
              color: mode === "loading" ? "var(--text-tertiary)" : "var(--source-local)",
              background: "color-mix(in srgb, var(--source-local) 10%, transparent)",
            }}
          >
            {mode === "loading" ? "working…" : "▶ ask"}
          </button>
        </div>
        <p className="mt-2 font-mono text-[10px]" style={{ color: "var(--text-tertiary)" }}>
          the system decides who handles this; you never pick
        </p>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {EXAMPLES.map((text) => (
            <button
              key={text}
              type="button"
              onClick={() => setRawInput(text)}
              className="rounded-full border px-2.5 py-1 text-left text-[11px] transition-colors"
              style={{ borderColor: "var(--border-hairline-strong)", color: "var(--text-secondary)" }}
            >
              {text}
            </button>
          ))}
        </div>
      </form>

      {mode === "error" && error && (
        <div
          data-testid="request-error"
          role="alert"
          className="rounded-lg border px-4 py-3 font-mono text-[12px]"
          style={{ borderColor: "var(--source-human)", color: "var(--source-human)", background: "color-mix(in srgb, var(--source-human) 8%, transparent)" }}
        >
          {error}
        </div>
      )}

      {response && (
        <ResultPanel response={response} onCheck={checkDecision} checking={checking} checkedStillPending={stillPending} />
      )}
      {response?.result.status === "needs_more_info" && response.result.follow_up && (
        <FollowUpForm
          key={response.request_id}
          followUp={response.result.follow_up}
          reason={response.result.reason}
          loading={mode === "loading"}
          onSubmit={submitFollowUp}
        />
      )}
      <TraceGraph trail={response?.audit_trail ?? []} />
    </div>
  );
}
