"""Simple, explainable quality indicators for agent-facing resolution drafts."""

from dataclasses import asdict, dataclass

from supportai.policy import PolicyReview
from supportai.resolution import ResolutionPlan


@dataclass
class QualityReport:
    score: int
    checks: dict[str, bool]
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_resolution(plan: ResolutionPlan, policy_review: PolicyReview) -> QualityReport:
    """Score four visible draft criteria; this is a heuristic, not a truth metric."""
    reply = plan.customer_reply.strip()
    reply_words = reply.split()
    checks = {
        "knowledge_support": bool(plan.sources),
        "actionable_next_step": any(
            action.strip() and "escalate to a support specialist" not in action.lower()
            for action in plan.actions
        ),
        "customer_ready_reply": len(reply_words) >= 18 and reply.lower().startswith("hi "),
        "policy_safe": not policy_review.blocking_findings,
    }
    notes = [
        label for key, label in (
            ("knowledge_support", "No matching support source was found."),
            ("actionable_next_step", "Add a concrete next step or information request."),
            ("customer_ready_reply", "Review the reply for completeness and a customer-ready greeting."),
            ("policy_safe", "Policy review found a blocking safety or policy issue."),
        ) if not checks[key]
    ]
    score = round(sum(checks.values()) / len(checks) * 100)
    return QualityReport(score=score, checks=checks, notes=notes)
