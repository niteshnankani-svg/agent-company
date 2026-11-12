import { NextResponse } from "next/server";

// Reports whether the Python API is reachable. It does not answer for it.
export const runtime = "nodejs";

export async function GET() {
  const base = process.env.AGENT_COMPANY_API_URL;
  if (!base) return NextResponse.json({ status: "not_configured" }, { status: 503 });
  try {
    const res = await fetch(`${base.replace(/\/$/, "")}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    return NextResponse.json({ status: res.ok ? "ok" : "unhealthy" }, { status: res.ok ? 200 : 502 });
  } catch {
    return NextResponse.json({ status: "unreachable" }, { status: 502 });
  }
}
