import { NextResponse } from "next/server";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";

export async function GET() {
  try {
    const upstream = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
    return new NextResponse(null, { status: upstream.ok ? 200 : 503 });
  } catch {
    return new NextResponse(null, { status: 503 });
  }
}
