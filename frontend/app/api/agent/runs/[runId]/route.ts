import { NextRequest, NextResponse } from "next/server";

// Read-only: where a request stands (still waiting for a person, or decided). A pass-through to the
// Python API. The reviewer actions (list pending, resume) are deliberately NOT exposed here: they need
// authentication, which the API gains in a later phase, and they are for staff, not for the public page.
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ runId: string }> }) {
  const base = process.env.AGENT_COMPANY_API_URL;
  if (!base) {
    return NextResponse.json({ detail: "The console is not connected to the Agent Company API." }, { status: 503 });
  }
  const { runId } = await params;
  // Run ids are server-generated 128-bit random hex strings; anything else is rejected without a call.
  if (!/^[0-9a-f]{32}$/.test(runId)) {
    return NextResponse.json({ detail: "Not a valid reference." }, { status: 400 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${base.replace(/\/$/, "")}/runs/${runId}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
  } catch (error) {
    console.error("Agent Company API unreachable:", error);
    return NextResponse.json({ detail: "The Agent Company API did not respond. Please try again shortly." }, { status: 502 });
  }
  if (upstream.status === 404) return NextResponse.json({ detail: "No request with that reference." }, { status: 404 });
  if (!upstream.ok) {
    console.error(`Agent Company API returned HTTP ${upstream.status}`);
    return NextResponse.json({ detail: `The Agent Company API could not look this up (HTTP ${upstream.status}).` }, { status: 502 });
  }
  return NextResponse.json(await upstream.json(), { status: 200 });
}
