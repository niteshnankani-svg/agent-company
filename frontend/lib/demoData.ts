import type { AgentResponse } from "./types";
import responses from "./demo-responses.json";

// Demo mode ships a set of real /request responses captured verbatim from the
// running Agent Company backend (orchestrator + DistilBERT triage + verifier),
// so the frontend can be deployed to Vercel with no backend to reach.
//
// It is ON by default whenever no live backend URL is configured (e.g. a plain
// `vercel deploy`), and forced on by NEXT_PUBLIC_DEMO_MODE=1. Set
// NEXT_PUBLIC_API_BASE_URL to a reachable backend to run against the real model.
export const DEMO_MODE =
  process.env.NEXT_PUBLIC_DEMO_MODE === "1" ||
  !process.env.NEXT_PUBLIC_API_BASE_URL;

const CAPTURED = responses as Record<string, AgentResponse>;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** The exact ticket strings that have a canned response (used by the UI hints). */
export const DEMO_TICKETS = Object.keys(CAPTURED);

/** Return the captured response for this raw_input, or null if none exists. */
export function demoResponseFor(rawInput: string): AgentResponse | null {
  const key = rawInput.trim();
  if (CAPTURED[key]) return clone(CAPTURED[key]);
  // tolerate trailing punctuation / whitespace differences
  const norm = key.replace(/\s+/g, " ").replace(/[.\s]+$/, "").toLowerCase();
  for (const [k, v] of Object.entries(CAPTURED)) {
    if (k.replace(/\s+/g, " ").replace(/[.\s]+$/, "").toLowerCase() === norm) {
      return clone(v);
    }
  }
  return null;
}
