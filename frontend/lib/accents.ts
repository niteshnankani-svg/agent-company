// Colours and wording for statuses. One place, used by the result panel and the trace.

export interface StatusMeta {
  label: string;
  description: string;
  color: string;
}

const GOOD = "var(--source-cache)";
const HUMAN = "var(--source-human)";
const WARN = "var(--source-llm)";
const NEUTRAL = "var(--source-local)";

export const STATUS_META: Record<string, StatusMeta> = {
  // result statuses
  answered: { label: "ANSWERED", description: "A department answered, with evidence.", color: GOOD },
  needs_human: { label: "NEEDS A PERSON", description: "The system declined to decide; a person must.", color: HUMAN },
  needs_more_info: { label: "NEEDS MORE INFO", description: "More details are needed to continue.", color: WARN },
  failed: { label: "COULD NOT COMPLETE", description: "A service the request depends on failed.", color: HUMAN },
  // trace statuses
  routed: { label: "ROUTED", description: "A department was chosen.", color: NEUTRAL },
  unrouted: { label: "NOT ROUTED", description: "No department could be chosen.", color: HUMAN },
  pass: { label: "PASS", description: "The verifier accepted the result.", color: GOOD },
  fail: { label: "FAIL", description: "The verifier rejected the result.", color: HUMAN },
  hit: { label: "CACHE HIT", description: "Reused a stored, verified answer.", color: GOOD },
  miss: { label: "CACHE MISS", description: "Nothing suitable was stored.", color: NEUTRAL },
  stored: { label: "STORED", description: "The verified answer was stored.", color: GOOD },
  skipped: { label: "SKIPPED", description: "Policy says this must not be cached.", color: NEUTRAL },
  not_cached: { label: "NOT CACHED", description: "Policy or status ruled out caching.", color: NEUTRAL },
  disabled: { label: "CACHE OFF", description: "No embedding provider is configured.", color: NEUTRAL },
};

export function statusMeta(status: string): StatusMeta {
  return (
    STATUS_META[status] ?? {
      label: status.toUpperCase().replaceAll("_", " "),
      description: "",
      color: "var(--text-secondary)",
    }
  );
}

// Plain-language versions of the machine-readable `reason` codes.
export const REASON_TEXT: Record<string, string> = {
  employee_complaint: "This looks like an employee complaint or grievance. These are always handled by a person.",
  credit_model_not_validated: "Credit decisions are not automated: the credit model has not been validated.",
  payment_requires_human_approval: "Payments are never approved automatically; a person must approve.",
  prompt_injection_suspected: "The text looked like an attempt to manipulate the system.",
  security_incident_review: "This mentions a possible security incident, so the Security Team must review it.",
  hs_code_not_identified: "No specific HS code could be identified for this product.",
  hs_code_not_in_tradestat: "The proposed HS code has no record in TradeStat.",
  tradestat_verification_unavailable: "The proposed HS code could not be checked against TradeStat right now.",
  no_relevant_sections: "No relevant legal sections were found.",
  citations_unreadable: "The legal sources came back in a form that could not be used.",
  hr_agent_returned_no_evidence: "An answer was produced but without evidence, so it is not released.",
  request_not_classified: "The system could not tell which team should handle this.",
  classifier_unavailable: "The service that decides where requests go is unavailable.",
  verifier_failed: "The answer could not be verified.",
};

export function reasonText(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return REASON_TEXT[reason] ?? reason.replaceAll("_", " ");
}
