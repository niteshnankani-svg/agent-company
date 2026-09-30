// In-process port of agent_company/orchestrator.py::local_classify and the
// backend_ticketing.py guards. Routes across the five departments via
// TypeSafe's Jev model; the Backend / Ticketing lane then runs the TF-IDF IT
// model. TYPESAFE_API_KEY must be set (server-side only — this file is only
// ever imported from the nodejs-runtime API route, never a client component).

import { choice, TypeSafeClient } from "@typesafe-ai/sdk";

export type Department =
  | "finance"
  | "hr_complaints"
  | "backend_ticketing"
  | "export_compliance"
  | "legal_compliance";

const typesafeClient = new TypeSafeClient();

// Descriptions derived from the department definitions this router used
// before Jev (see git history for the original keyword lists); kept
// identical to agent_company/orchestrator.py's _DEPARTMENT_CRITERIA.
const DEPARTMENT_CRITERIA: Record<Department, string> = {
  finance:
    "Invoices, payments, credit, loans, supplier/vendor payables, refunds, " +
    "charges, billing, reimbursement, ledger entries, accounts receivable " +
    "or payable, purchase orders",
  hr_complaints:
    "Employee/HR matters: complaints, grievances, harassment, " +
    "discrimination, bullying, workplace conflicts (always escalated to " +
    "a human) as well as general informational questions about " +
    "employees or HR data - headcounts, department stats, payroll, " +
    "onboarding, hiring, benefits, leave requests (answered directly " +
    "when possible)",
  export_compliance:
    "HS codes, exports, shipments, customs, tariffs, imports, freight, " +
    "shipping, bill of lading, incoterms, duty drawback",
  legal_compliance:
    "Statutes (BNS, BNSS, BSA, DPDP), legal sections, contracts, " +
    "compliance, regulation, NDAs, liability, lawsuits, clauses, " +
    "counsel, law/legal questions",
  backend_ticketing:
    "IT and technical issues: errors, bugs, tickets, system outages, " +
    "crashes, servers, timeouts, login/sign-in problems, uploads, " +
    "databases, APIs, VPN, passwords, hardware (laptop, desktop, " +
    "printer, keyboard, monitor), software, network, wifi, routers, " +
    "firewalls, malware, phishing, access, applications, authentication, " +
    "browsers, data breaches",
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
}

export async function localClassify(text: string): Promise<OrchestratorResult> {
  const response = await typesafeClient.systemOne({
    state: text,
    questions: {
      department: choice(
        "Which department should handle this request?",
        DEPARTMENT_CRITERIA,
      ),
    },
  });
  const answer = response.answers.department;
  return { department: answer.choice, confidence: answer.confidence };
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
