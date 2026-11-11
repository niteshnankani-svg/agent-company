# Agent Company console

A single free-text box in front of the Agent Company Python API. You type a request;
the Python graph decides who handles it. You never choose a department.

The console contains **no routing or answering logic**. `app/api/agent/request/route.ts`
forwards `{request_id, raw_input}` to the Python API and returns what it says; if the API
is down you are told so. The browser calls this route rather than the API directly so the
API address (and, later, its key) stay on the server.

What it shows for each request:

- the result, by status: `answered` (answer and the evidence relied on), `needs_human`
  (why a person must decide), `needs_more_info` (which details are missing), `failed`
  (which service failed);
- a confidence figure **only** when the department has a real one, labelled with what it is;
- an animated replay of the real `audit_trail` the graph produced (nothing is invented).

## Run it

```bash
npm install
cp .env.local.example .env.local     # set AGENT_COMPANY_API_URL
npm run dev                          # http://localhost:3000
```

## Tests

```bash
npm run test:e2e
```

These use a small mock of the Python API started by the test run, so they need no backend
and no keys. They check the UI for each status, that the route forwards only the sentence
and an id, that API failures are reported without leaking upstream text, and that no router
code exists in the frontend.
