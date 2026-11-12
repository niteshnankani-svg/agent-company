// A stand-in for the Python API, used only by the Playwright tests.
// What it returns is chosen by a keyword in the request text, and it remembers the
// last request body so tests can check exactly what the console forwarded.
import http from "node:http";

const PORT = Number(process.env.MOCK_API_PORT || 8099);
let last = null;
let lastAuth = null;
let counter = 0;
const KEY = process.env.MOCK_API_KEY; // when set, /request and /runs require this bearer token
const runs = new Map(); // run_id -> { response, resolved }
const newRunId = () => (++counter).toString(16).padStart(32, "0");
const decided = (response) => ({
  ...response, pending_review: false, needs_human: false,
  result: { ...response.result, status: "answered", answer: "A person reviewed this and replied: please contact IT.", reason: null, evidence: [{ source: "human_review", text: null, data: { decision: "answer", reviewer: "asha" } }] },
  verification: { status: "human_provided", check: "human_review", reviewer: "asha" },
});

const trail = (id, extra = []) => [
  { node: "orchestrator", status: "routed", request_id: id, department: "legal_compliance", source: "local_model" },
  ...extra,
  { node: "verifier", status: "pass", request_id: id, check: "structure_only" },
];
const base = (department, status, rest) => ({
  department, status, answer: null, evidence: [], confidence: { kind: "none", value: null },
  missing_fields: [], error: null, reason: null, ...rest,
});
const reply = (id, result, nodes, verification = null) => ({
  request_id: id, result, needs_human: result.status === "needs_human", verification,
  escalation_reason: null, department_history: [], audit_trail: trail(id, nodes),
});

const FORM_FIELDS = [
  { name: "vendor_is_first_time", label: "Is this the first time we are paying this vendor?", kind: "boolean", help: null, choices: null, problem: null },
  { name: "vendor_average_invoice", label: "What this vendor usually invoices (₹)", kind: "number", help: "Leave blank for a first-time vendor.", choices: null, problem: null },
  { name: "payment_terms_days", label: "Payment terms (days)", kind: "integer", help: null, choices: null, problem: null },
];
const formResult = (fields, reason, problems = {}) => base("finance", "needs_more_info", {
  missing_fields: fields.map((f) => f.name), reason,
  follow_up: { sub_department: "ap", collected: { invoice_amount: 45000 }, fields: fields.map((f) => ({ ...f, problem: problems[f.name] ?? null })) },
});

function respondFollowUp(body) {
  const f = body.follow_up.fields;
  if (f.payment_terms_days === 99999)
    return reply(body.request_id, formResult(FORM_FIELDS, "invalid_fields", { payment_terms_days: "Input should be less than or equal to 3650" }));
  return reply(body.request_id, base("finance", "needs_human", {
    reason: "payment_requires_human_approval",
    answer: "AP policy recommendation: approve (no_defined_policy_flags). Human approval required; no payment executed.",
  }));
}

function respond(text, id) {
  if (text.includes("form-unavailable"))
    return reply(id, formResult(FORM_FIELDS, "extraction_unavailable"));
  if (text.includes("form"))
    return reply(id, formResult(FORM_FIELDS, "missing_fields"));
  if (text.includes("answered-legal"))
    return reply(id, base("legal_compliance", "answered", {
      answer: "Section 357 of the BNS covers **breach of contract** to attend on a helpless person.",
      evidence: [{ source: "legal_rag:BNS Section 357", text: "Breach of contract: punishes failing to supply the wants of a helpless person.", data: { score: 0.64 } }],
      confidence: { kind: "retrieval_score", value: 0.64 },
    }), [{ node: "legal_head", status: "answered", request_id: id }], { status: "verified", check: "claims" });
  if (text.includes("unchecked-backend"))
    return reply(id, base("backend_ticketing", "answered", {
      answer: "Hold the power button for ten seconds.",
      evidence: [{ source: "ticketing_agent", text: null, data: { category: "hardware" } }],
    }), [], { status: "unchecked", check: "no_source_material" });
  if (text.includes("withdrawn"))
    return reply(id, base("legal_compliance", "needs_human", {
      reason: "verification_failed", answer: "The fine is five thousand rupees.",
      evidence: [
        { source: "legal_rag:BNS Section 357", text: "Breach of contract to attend on a helpless person.", data: null },
        { source: "verifier", text: "- \"the fine is five thousand rupees\" (not found in the evidence)", data: null },
      ],
    }), [], { status: "failed", check: "claims" });
  if (text.includes("answered-hr"))
    return reply(id, base("hr_complaints", "answered", {
      answer: "There are 1035 employees in Sales.",
      evidence: [{ source: "hr_agent:sql_rag", text: "SELECT COUNT(*) ... -> 1035", data: null }],
    }));
  if (text.includes("human"))
    return reply(id, base("backend_ticketing", "needs_human", { reason: "security_incident_review" }));
  if (text.includes("declined"))
    return reply(id, base("backend_ticketing", "failed", { reason: "declined_by_reviewer", error: "Declined by a reviewer: out of scope" }));
  if (text.includes("more-list"))
    return reply(id, base("finance", "needs_more_info", { missing_fields: ["credit_score", "loan_amount"], reason: "missing_fields" }));
  if (text.includes("failed"))
    return reply(id, base("legal_compliance", "failed", { error: "legal_rag: request_failed (All connection attempts failed)" }));
  return reply(id, base("legal_compliance", "needs_human", { reason: "request_not_classified" }));
}

http.createServer((req, res) => {
  if (KEY && (req.url === "/request" || req.url.startsWith("/runs/"))) {
    lastAuth = req.headers.authorization ?? null;
    if (lastAuth !== `Bearer ${KEY}`) {
      res.writeHead(401, { "Content-Type": "application/json" });
      return res.end('{"detail":"Missing or invalid API key."}');
    }
  }
  if (req.url === "/__auth") { res.writeHead(200, { "Content-Type": "application/json" }); return res.end(JSON.stringify({ lastAuth })); }
  if (req.url === "/health") { res.writeHead(200, { "Content-Type": "application/json" }); return res.end('{"status":"ok"}'); }
  const run = req.url.match(/^\/runs\/([0-9a-f]{32})$/);
  if (run) {
    const entry = runs.get(run[1]);
    res.writeHead(entry ? 200 : 404, { "Content-Type": "application/json" });
    return res.end(JSON.stringify(entry ? (entry.resolved ? decided(entry.response) : entry.response) : { detail: "No such run." }));
  }
  const resolve = req.url.match(/^\/__resolve\/([0-9a-f]{32})$/);
  if (resolve) {
    if (runs.has(resolve[1])) runs.get(resolve[1]).resolved = true;
    res.writeHead(200); return res.end("ok");
  }
  if (req.url === "/__last") { res.writeHead(200, { "Content-Type": "application/json" }); return res.end(JSON.stringify(last)); }
  if (req.url === "/request" && req.method === "POST") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      last = JSON.parse(body);
      if (last.raw_input.includes("upstream-down")) {
        res.writeHead(500, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ detail: "Request processing failed: SECRET_UPSTREAM_TRACEBACK at /srv/app/graph.py" }));
      }
      const response = last.follow_up ? respondFollowUp(last) : respond(last.raw_input, last.request_id);
      response.run_id = newRunId();
      response.pending_review = response.result.status === "needs_human";
      runs.set(response.run_id, { response, resolved: false });
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(response));
    });
    return;
  }
  res.writeHead(404); res.end();
}).listen(PORT, "127.0.0.1", () => console.log(`mock API on ${PORT}`));
