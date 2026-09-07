import { NextRequest, NextResponse } from "next/server";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";

// The real backend does not send CORS headers, so the browser can't call it
// directly. This route is a thin same-origin pass-through — the request body
// goes to the backend unmodified and the JSON response comes back unmodified.
export async function POST(req: NextRequest) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE_URL}/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  } catch (err) {
    return NextResponse.json(
      { error: `Could not reach the Agent Company API at ${API_BASE_URL}: ${(err as Error).message}` },
      { status: 502 },
    );
  }

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
