import { NextRequest, NextResponse } from "next/server";

// A pass-through to the Python API, and nothing else.
//
// There is deliberately no routing, no classification and no fallback answer in
// this file. If the Python API is down, the user is told so; they are never shown
// an answer from some other system. The browser talks to this route (not to Python
// directly) so the API address, and later the API key, stay on the server.
export const runtime = "nodejs";
export const maxDuration = 60;

const MAX_INPUT_CHARS = 8000;

type FollowUp = { department: string; sub_department: string | null; fields: Record<string, unknown> };

// Shape check only (strings, numbers, booleans, null; bounded size). Whether the department
// is real and the values make sense is the Python API's decision.
function parseFollowUp(value: unknown): FollowUp | null | "invalid" {
  if (value === undefined || value === null) return null;
  if (typeof value !== "object") return "invalid";
  const v = value as Record<string, unknown>;
  if (typeof v.department !== "string" || !v.department || v.department.length > 64) return "invalid";
  if (v.sub_department !== null && v.sub_department !== undefined && typeof v.sub_department !== "string") return "invalid";
  if (!v.fields || typeof v.fields !== "object" || Array.isArray(v.fields)) return "invalid";
  const entries = Object.entries(v.fields as Record<string, unknown>);
  if (entries.length > 50) return "invalid";
  for (const [key, val] of entries) {
    if (key.length > 64) return "invalid";
    if (val !== null && !["string", "number", "boolean"].includes(typeof val)) return "invalid";
    if (typeof val === "string" && val.length > 200) return "invalid";
  }
  return {
    department: v.department,
    sub_department: (v.sub_department as string | null | undefined) ?? null,
    fields: v.fields as Record<string, unknown>,
  };
}

export async function POST(req: NextRequest) {
  const base = process.env.AGENT_COMPANY_API_URL;
  if (!base) {
    return NextResponse.json(
      { detail: "The console is not connected to the Agent Company API (AGENT_COMPANY_API_URL is not set)." },
      { status: 503 },
    );
  }

  let body: { request_id?: unknown; raw_input?: unknown; follow_up?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  const rawInput = typeof body.raw_input === "string" ? body.raw_input : "";
  if (!rawInput.trim()) {
    return NextResponse.json({ detail: "Please type a request." }, { status: 400 });
  }
  if (rawInput.length > MAX_INPUT_CHARS) {
    return NextResponse.json({ detail: `Please keep the request under ${MAX_INPUT_CHARS} characters.` }, { status: 400 });
  }
  const followUp = parseFollowUp(body.follow_up);
  if (followUp === "invalid") {
    return NextResponse.json({ detail: "The follow-up answers were not in the expected form." }, { status: 400 });
  }
  const requestId =
    typeof body.request_id === "string" && body.request_id
      ? body.request_id
      : "req-" + Math.random().toString(36).slice(2, 8);

  const timeoutMs = Number(process.env.AGENT_COMPANY_TIMEOUT_MS) || 55_000;
  let upstream: Response;
  try {
    upstream = await fetch(`${base.replace(/\/$/, "")}/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Only the sentence, an id and (for a follow-up form) the user's answers are forwarded.
      // The Python API decides everything else.
      body: JSON.stringify({
        request_id: requestId,
        raw_input: rawInput,
        ...(followUp ? { follow_up: followUp } : {}),
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    console.error("Agent Company API unreachable:", error);
    return NextResponse.json(
      { detail: "The Agent Company API did not respond. Please try again shortly." },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    console.error(`Agent Company API returned HTTP ${upstream.status}`);
    // The upstream body is not forwarded: it is not for end users.
    return NextResponse.json(
      { detail: `The Agent Company API could not process this request (HTTP ${upstream.status}).` },
      { status: 502 },
    );
  }
  return NextResponse.json(await upstream.json(), { status: 200 });
}
