import { NextResponse } from "next/server";

// The orchestrator + IT-triage model run in this deployment's own serverless
// runtime, so there is no external backend to health-check.
export const runtime = "nodejs";

export async function GET() {
  return NextResponse.json({ status: "ok" }, { status: 200 });
}
