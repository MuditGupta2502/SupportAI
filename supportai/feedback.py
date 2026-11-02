"""Summarize human feedback on SupportAI's suggestions."""

from collections import Counter


def build_feedback_report(tickets) -> dict:
    """Compare saved triage suggestions with human-reviewed outcomes."""
    reviewed = [ticket for ticket in tickets if ticket.review_status in {"confirmed", "corrected"}]
    corrections = [ticket for ticket in reviewed if ticket.review_status == "corrected"]
    category_matches = sum(ticket.suggested_category == ticket.category for ticket in reviewed)
    urgency_matches = sum(ticket.suggested_urgency == ticket.urgency for ticket in reviewed)
    category_corrections = Counter(
        f"{ticket.suggested_category} -> {ticket.category}"
        for ticket in corrections if ticket.suggested_category != ticket.category
    )
    urgency_corrections = Counter(
        f"{ticket.suggested_urgency} -> {ticket.urgency}"
        for ticket in corrections if ticket.suggested_urgency != ticket.urgency
    )

    resolution_reviewed = [ticket for ticket in tickets if ticket.resolution_status in {"approved", "rejected"}]
    draft_feedback = [ticket for ticket in resolution_reviewed if ticket.resolution_draft.strip()]
    unchanged = sum(
        ticket.resolution_status == "approved"
        and ticket.approved_reply.strip() == ticket.resolution_draft.strip()
        for ticket in draft_feedback
    )
    edited = sum(
        ticket.resolution_status == "approved"
        and ticket.approved_reply.strip() != ticket.resolution_draft.strip()
        for ticket in draft_feedback
    )
    rejected = sum(ticket.resolution_status == "rejected" for ticket in draft_feedback)
    unchanged_scores = [
        ticket.resolution_quality_score for ticket in draft_feedback
        if ticket.resolution_status == "approved"
        and ticket.approved_reply.strip() == ticket.resolution_draft.strip()
        and ticket.resolution_quality_score is not None
    ]
    changed_scores = [
        ticket.resolution_quality_score for ticket in draft_feedback
        if (ticket.resolution_status == "rejected" or (
            ticket.resolution_status == "approved"
            and ticket.approved_reply.strip() != ticket.resolution_draft.strip()
        )) and ticket.resolution_quality_score is not None
    ]

    return {
        "total_tickets": len(tickets),
        "triage_reviewed": len(reviewed),
        "triage_pending": sum(ticket.review_status == "pending" for ticket in tickets),
        "triage_corrected": len(corrections),
        "category_accuracy": category_matches / len(reviewed) if reviewed else None,
        "urgency_accuracy": urgency_matches / len(reviewed) if reviewed else None,
        "category_corrections": category_corrections,
        "urgency_corrections": urgency_corrections,
        "resolution_approved": sum(ticket.resolution_status == "approved" for ticket in tickets),
        "resolution_rejected": sum(ticket.resolution_status == "rejected" for ticket in tickets),
        "resolution_pending": sum(ticket.resolution_status == "pending" for ticket in tickets),
        "resolution_draft_feedback": len(draft_feedback),
        "resolution_unchanged": unchanged,
        "resolution_edited": edited,
        "resolution_rejected_with_draft": rejected,
        "quality_scored_reviews": len(unchanged_scores) + len(changed_scores),
        "quality_avg_unchanged": sum(unchanged_scores) / len(unchanged_scores) if unchanged_scores else None,
        "quality_avg_corrected_or_rejected": sum(changed_scores) / len(changed_scores) if changed_scores else None,
    }
