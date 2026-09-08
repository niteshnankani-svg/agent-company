// In-process port of agent_company/orchestrator.py::local_classify and the
// backend_ticketing.py guards. Deterministic keyword routing across the five
// departments; the Backend / Ticketing lane then runs the TF-IDF IT model.

export type Department =
  | "finance"
  | "hr_complaints"
  | "backend_ticketing"
  | "export_compliance"
  | "legal_compliance";

// Order matters: ties resolve to the first department, so the specific business
// departments come before the broad IT catch-all.
export const KEYWORD_MAP: Record<Department, string[]> = {
  finance: [
    "invoice", "payment", "credit", "loan", "supplier payable", "vendor", "refund",
    "charged", "billing", "reimbursement", "ledger", "payable", "accounts receivable",
    "accounts payable", "purchase order",
  ],
  hr_complaints: [
    "complaint", "grievance", "harassment", "employee issue", "payroll", "onboarding",
    "hiring", "benefits", "leave request", "manager", "coworker", "colleague",
    "workplace", "discrimination", "bullying",
  ],
  export_compliance: [
    "hs code", "export", "shipment", "customs", "tariff", "import", "freight",
    "shipping", "bill of lading", "incoterm", "duty drawback",
  ],
  legal_compliance: [
    "bns", "bnss", "bsa", "dpdp", "murder", "section", "punishment", "legal", "law",
    "act", "contract", "compliance", "regulation", "nda", "liability", "lawsuit",
    "clause", "counsel", "statute",
  ],
  backend_ticketing: [
    "error", "bug", "ticket", "system down", "crash", "server", "timeout", "login",
    "log in", "sign in", "upload", "database", "api", "vpn", "password", "laptop",
    "computer", "desktop", "printer", "hardware", "software", "network", "wifi",
    "wi-fi", "router", "firewall", "malware", "ransomware", "phishing", "access",
    "application", "ethernet", "dns", "keyboard", "monitor", "battery", "screen",
    "shared folder", "authentication", "browser", "brute force", "data breach",
    "outlook", "email not", "cannot connect", "won't boot", "not working",
    "reset my", "blue screen", "frozen", "reboot",
  ],
};

export const DEPT_NODE: Record<Department, string> = {
  finance: "finance_head",
  hr_complaints: "hr_head",
  backend_ticketing: "backend_head",
  export_compliance: "export_head",
  legal_compliance: "legal_head",
};

export const DEPT_LABEL: Record<Department, string> = {
  finance: "Finance",
  hr_complaints: "HR / Complaints",
  backend_ticketing: "Backend / Ticketing",
  export_compliance: "Export / Compliance",
  legal_compliance: "Legal / Compliance",
};

export interface OrchestratorResult {
  department: Department | null;
  confidence: number;
  hits: string[];
}

export function localClassify(text: string): OrchestratorResult {
  const lo = text.toLowerCase();
  let best: Department | null = null;
  let bestCount = 0;
  let hits: string[] = [];
  for (const dept of Object.keys(KEYWORD_MAP) as Department[]) {
    const h = KEYWORD_MAP[dept].filter((k) => lo.includes(k));
    if (h.length > bestCount) {
      bestCount = h.length;
      best = dept;
      hits = h;
    }
  }
  if (bestCount === 0) return { department: null, confidence: 0, hits: [] };
  return { department: best, confidence: Math.min(0.6 + 0.15 * bestCount, 0.95), hits };
}

// Guards from backend_ticketing.py — run before the model.
export const INJECTION_RE =
  /ignore\s+(all\s+)?(previous|prior|system)|system\s*prompt|reveal\s+.*secret|bypass\s+.*(auth|guard)/i;
export const SECURITY_RE =
  /\b(malware|ransomware|phishing|breach|botnet|ddos|intrusion|keylogger)\b|compromised\s+credentials|unauthorized\s+access|suspicious\s+|brute\s+force/i;

export function scrubPii(text: string): string {
  return text
    .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, "<EMAIL>")
    .replace(/\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b/g, "<PHONE>")
    .replace(/\b(?:\d[ -]*?){13,16}\b/g, "<CARD>");
}

export const ROUTE_MARGIN = 0.65;
