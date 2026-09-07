import type { AgentResponse } from "./types";
import responses from "./demo-responses.json";

// Demo mode ships a set of real /request responses captured verbatim from the
// running Agent Company backend (orchestrator + DistilBERT triage + verifier),
// so the frontend can be deployed to Vercel with no backend to reach.
//
// FORCE_DEMO (NEXT_PUBLIC_DEMO_MODE=1) skips the backend entirely and always
// replays a captured response. Without it, lib/api.ts calls the live backend
// first and only falls back to a captured response when the call fails — so a
// deploy is "live" whenever NEXT_PUBLIC_API_BASE_URL points at a running
// backend, and still works (on captured real data) when it does not.
export const FORCE_DEMO = process.env.NEXT_PUBLIC_DEMO_MODE === "1";

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
