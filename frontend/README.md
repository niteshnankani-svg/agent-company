# Agent Company — Trace Console

A Next.js frontend for the Agent Company multi-department AI decision system.
It calls the real `/request` backend API. The core feature is
`components/TraceGraph.tsx`, which replays the exact `audit_trail` array
returned by the backend as an animated graph traversal (orchestrator →
department head → verifier, looping back on retries), alongside
`components/BackendTriage.tsx` which renders the DistilBERT 5-class
distribution and accept gate whenever a response carries `backend_triage`.

### Demo mode (no backend)

When `NEXT_PUBLIC_API_BASE_URL` is unset — or `NEXT_PUBLIC_DEMO_MODE=1` — the
app runs entirely on `lib/demo-responses.json`: real `/request` responses
captured verbatim from the running backend for the 9 sample tickets. Every
panel is live; only inference on arbitrary typed text is disabled. This is
what a plain Vercel deploy renders.

## Deploy to Vercel

```bash
cd frontend
npx vercel            # first run links/creates the project, then deploys a preview
npx vercel --prod     # promote to production
```

No env vars needed for the standalone demo. To point the deploy at a live
backend instead, set `NEXT_PUBLIC_API_BASE_URL` in the Vercel project
(Settings → Environment Variables, or `npx vercel env add NEXT_PUBLIC_API_BASE_URL`)
and redeploy. If you connect the whole repo (not just `frontend/`), set the
project's **Root Directory** to `frontend`.

## Setup

```bash
npm install
cp .env.local.example .env.local
# edit .env.local and set the real backend address, e.g.:
# NEXT_PUBLIC_API_BASE_URL=http://203.0.113.10:8080
npm run dev
```

Open http://localhost:3000. The header shows the configured API base URL and
a live `/health` status dot.

**Note on CORS:** the backend does not send `Access-Control-Allow-Origin`
headers, so a browser can't call it directly (curl works fine — CORS is
browser-enforced only). The frontend calls same-origin routes
(`app/api/agent/request`, `app/api/agent/health`) that proxy to
`NEXT_PUBLIC_API_BASE_URL` server-side and pass the request/response through
byte-for-byte. If the backend adds CORS headers later, this proxy can be
removed and `lib/api.ts` pointed back at the backend directly.

## Flows implemented

- **Finance — Accounts Receivable** (single department, `sub_department: "ar"`)
- **Export / Compliance** (single department, HS code resolution)
- **Legal / Compliance** (single department, BNS/IPC statutory lookup)
- **Sequential — Export → Finance** (multi-department, `department_sequence`)

Each scenario's `raw_input` and department fields are editable before
dispatch. HR/Backend-Ticketing are intentionally not included yet.

## How the graph replay works

The backend is not streaming — it returns one complete JSON response. After
that response lands, `TraceGraph` walks the real `audit_trail` array one
entry at a time (~550ms/step), revealing nodes in first-seen order and
drawing a dashed "RETRY" arc whenever a node (typically `verifier`) is
revisited. No intermediate states are invented — only entries that are
actually present in the returned `audit_trail` are ever shown. A "↺ replay"
button re-runs the animation from the cached response without calling the
API again.

## Tests

Playwright end-to-end tests call the **real, running backend** — set
`NEXT_PUBLIC_API_BASE_URL` in `.env.local` first (the dev server it starts
reads that file). Nothing is mocked.

```bash
npx playwright install --with-deps chromium   # first time only
npm run test:e2e
```

- `tests/finance.spec.ts` — Finance (AR) request renders a final decision
- `tests/export.spec.ts` — Export/Compliance resolves an HS code and shows
  the orchestrator/export_head nodes in the trace
- `tests/sequential.spec.ts` — sequential Export→Finance renders both
  department cards with `department_history` populated
