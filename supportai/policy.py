"""Rule-based safety checks for resolution plans and submitted customer replies."""

import re
from dataclasses import dataclass, field

from supportai.domains import get_domain
from supportai.resolution import ResolutionPlan


@dataclass
class PolicyReview:
    passed: bool
    findings: list[str]
    escalation_recommended: bool
    escalation_reasons: list[str]
    blocking_findings: list[str] = field(default_factory=list)


def review_plan(plan: ResolutionPlan) -> PolicyReview:
    """Flag unsafe promises as blocking; surface missing evidence as an advisory."""
    findings: list[str] = []
    blocking: list[str] = []
    escalation_reasons: list[str] = []
    reply = plan.customer_reply.casefold()
    actions = " ".join(plan.actions).casefold()

    def block(message: str) -> None:
        findings.append(message)
        blocking.append(message)

    promise_patterns = (
        r"\bwe(?:'ve| have) refunded\b", r"\bwe(?:'ll| will) refund\b",
        r"\bwe(?:'ve| have) replaced\b", r"\bwe(?:'ll| will) replace\b",
        r"\byour refund is approved\b", r"\bguaranteed (?:refund|replacement)\b",
        r"\byour (?:booking|plan|sim) (?:is|has been) (?:changed|confirmed|activated)\b",
    )
    if any(re.search(pattern, reply) for pattern in promise_patterns):
        block("The reply promises a refund, replacement, or account change before verification.")

    sensitive_patterns = (
        r"\b(?:send|share|tell|provide|enter)\s+(?:us\s+)?your\s+(?:password|passcode|otp|one[- ]time code)\b",
        r"\b(?:full\s+)?(?:credit\s+|debit\s+|payment\s+)?card number\b", r"\b(?:cvv|cvc|security code)\b",
        r"\bsocial security number\b", r"\bfull medical record\b",
    )
    if any(re.search(pattern, reply) for pattern in sensitive_patterns):
        block("The reply requests sensitive credentials, payment data, identity data, or health records.")

    if not plan.sources:
        findings.append("No matching knowledge source supports this draft; specialist review is recommended.")
        escalation_reasons.append("No matching knowledge source supports this draft.")

    if any("escalat" in action for action in plan.actions):
        findings.append("The plan recommends escalation to a support specialist.")
        escalation_reasons.extend(action for action in plan.actions if "escalat" in action.casefold())

    domain = get_domain(plan.domain)
    if plan.domain == "healthcare" and re.search(
        r"\b(?:take|stop taking|increase|decrease|double|skip)\s+(?:your\s+)?(?:medication|medicine|dose|dosage|prescription)\b|\byou (?:have|probably have) (?:a|an) diagnosis\b",
        reply,
    ):
        block("Healthcare policy: do not diagnose or direct medication use; route clinical questions to a qualified clinician.")
    checks = domain["policy_checks"]
    for phrase in checks["blocked_reply_phrases"]:
        if phrase.casefold() in reply:
            block(f"{domain['name']} policy: avoid unsupported promise or sensitive guidance ({phrase}).")

    issue_and_actions = f"{plan.issue} {actions}".casefold()
    for term in checks["escalation_terms"]:
        if term.casefold() in issue_and_actions:
            reason = f"{domain['name']} policy escalation trigger: '{term}'."
            if reason not in escalation_reasons:
                escalation_reasons.append(reason)
            if "escalat" not in actions:
                findings.append(f"{domain['name']} policy: this case should be escalated because it mentions '{term}'.")

    return PolicyReview(
        passed=not findings,
        findings=list(dict.fromkeys(findings)),
        escalation_recommended=bool(escalation_reasons),
        escalation_reasons=list(dict.fromkeys(escalation_reasons)),
        blocking_findings=list(dict.fromkeys(blocking)),
    )


def review_submitted_reply(ticket, reply: str) -> PolicyReview:
    """Re-run policy checks against the exact reply submitted for approval."""
    plan = ResolutionPlan(
        ticket_id=ticket.id,
        domain=ticket.domain,
        domain_name=get_domain(ticket.domain)["name"],
        customer_name=ticket.customer_name,
        issue=ticket.message,
        actions=[],
        questions=[],
        customer_reply=reply,
        sources=[],
    )
    return review_plan(plan)
