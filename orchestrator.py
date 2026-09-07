try:
    from cache import DepartmentCache
    from state import GraphState, log_audit
except ImportError:
    from .cache import DepartmentCache
    from .state import GraphState, log_audit


orchestrator_cache = DepartmentCache(
    department="orchestrator", similarity_threshold=0.90
)

DEPARTMENTS = [
    "finance",
    "hr_complaints",
    "backend_ticketing",
    "export_compliance",
    "legal_compliance",
]

# Order matters: local_classify() breaks ties by first-seen department, so the
# specific business departments are listed before backend_ticketing (the broad
# IT catch-all). Widened from the 5-word backend list that used to send every
# unmatched IT ticket to the Bedrock fallback. See docs/ROUTING_REGRESSION.md.
_KEYWORD_MAP = {
    "finance": [
        "invoice", "payment", "credit", "loan", "supplier payable", "vendor",
        "refund", "charged", "billing", "reimbursement", "ledger", "payable",
        "accounts receivable", "accounts payable", "purchase order",
    ],
    "hr_complaints": [
        "complaint", "grievance", "harassment", "employee issue", "payroll",
        "onboarding", "hiring", "benefits", "leave request", "manager",
        "coworker", "colleague", "workplace", "discrimination", "bullying",
    ],
    "export_compliance": [
        "hs code", "export", "shipment", "customs", "tariff", "import",
        "freight", "shipping", "bill of lading", "incoterm", "duty drawback",
    ],
    "legal_compliance": [
        "bns",
        "bnss",
        "bsa",
        "dpdp",
        "murder",
        "section",
        "punishment",
        "legal",
        "law",
        "act",
        "contract",
        "compliance",
        "regulation",
        "nda",
        "liability",
        "lawsuit",
        "clause",
        "counsel",
        "statute",
    ],
    "backend_ticketing": [
        "error", "bug", "ticket", "system down", "crash", "server", "timeout",
        "login", "log in", "sign in", "upload", "database", "api", "vpn",
        "password", "laptop", "computer", "desktop", "printer", "hardware",
        "software", "network", "wifi", "wi-fi", "router", "firewall",
        "malware", "ransomware", "phishing", "access", "application",
        "ethernet", "dns", "keyboard", "monitor", "battery", "screen",
        "shared folder", "authentication", "browser", "brute force",
        "data breach", "outlook", "email not", "cannot connect", "won't boot",
        "not working", "reset my", "blue screen", "frozen", "reboot",
    ],
}


def local_classify(text: str) -> tuple[str | None, float]:
    # Deterministic keyword classifier — swap for a trained DistilBERT model
    # with this exact same function signature; nothing downstream needs to change.
    lowered = text.lower()
    counts = {
        department: sum(keyword in lowered for keyword in keywords)
        for department, keywords in _KEYWORD_MAP.items()
    }
    department, count = max(counts.items(), key=lambda item: item[1])
    if count == 0:
        return None, 0.0
    confidence = min(0.6 + 0.15 * count, 0.95)
    return department, confidence


ORCHESTRATOR_PROMPT = """Respond with EXACTLY ONE department name from this list:
finance, hr_complaints, backend_ticketing, export_compliance, legal_compliance
Respond with nothing else: no punctuation and no explanation. If the request
spans multiple departments, pick the one owning the primary decision. If you
cannot confidently classify it, respond exactly: human_escalation."""


def llm_classify(text: str, state: GraphState) -> tuple[str, float]:
    if state["llm_budget_remaining"] <= 0:
        return "human_escalation", 0.0

    try:
        from anthropic import AnthropicBedrock

        client = AnthropicBedrock(aws_region="us-east-1")
        # Decremented before the attempt — a failed call still counts.
        state["llm_budget_remaining"] -= 1
        response = client.messages.create(
            model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            max_tokens=100,
            system=ORCHESTRATOR_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        department = response.content[0].text.strip().lower()
        if department in DEPARTMENTS:
            return department, 0.85
        return "human_escalation", 0.85
    except Exception as exc:
        print(f"Anthropic call skipped/failed: {exc}")
        return "human_escalation", 0.0


def orchestrator_node(state: GraphState) -> GraphState:
    cached = orchestrator_cache.get(state["raw_input"])
    if cached:
        state["request_type"] = cached["department"]
        log_audit(state, "orchestrator", cached["department"], source="cache")
        return state

    department, confidence = local_classify(state["raw_input"])
    if department is not None and confidence >= 0.75:
        state["request_type"] = department
        log_audit(state, "orchestrator", department, source="local_model")
        orchestrator_cache.set(state["raw_input"], {"department": department})
        return state

    department, confidence = llm_classify(state["raw_input"], state)
    state["request_type"] = department
    log_audit(
        state,
        "orchestrator",
        department,
        source="llm" if confidence > 0 else "human_escalation",
    )
    return state
