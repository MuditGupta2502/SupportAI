"""Basic ticket intake, rule-based triage, and human review for SupportAI."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from supportai.domains import get_domain


URGENCIES = {"low", "normal", "high", "critical"}
TICKET_STATUSES = {"open", "in_progress", "waiting_on_customer", "escalated", "resolved", "closed"}
STATUS_TRANSITIONS = {
    "open": {"in_progress", "waiting_on_customer", "escalated", "resolved"},
    "in_progress": {"open", "waiting_on_customer", "escalated", "resolved"},
    "waiting_on_customer": {"open", "in_progress", "escalated", "resolved"},
    "escalated": {"in_progress", "waiting_on_customer", "resolved"},
    "resolved": {"open", "closed"},
    "closed": {"open"},
}

SLA_HOURS = {"critical": 1, "high": 4, "normal": 24, "low": 72}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_conversation_entry(ticket: "Ticket", *, author: str, role: str, body: str,
                          visibility: str = "public", kind: str = "message") -> dict:
    """Append a validated public conversation message or internal activity entry."""
    body = body.strip()
    if not body:
        raise ValueError("Comment cannot be empty.")
    if len(body) > 4000:
        raise ValueError("Comment must be no longer than 4,000 characters.")
    if visibility not in {"public", "internal"} or kind not in {"message", "status"}:
        raise ValueError("Invalid conversation entry type.")
    entry = {
        "id": str(uuid4()), "author": author[:160], "role": role,
        "body": body, "visibility": visibility, "kind": kind,
        "created_at": utc_now(),
    }
    ticket.conversation.append(entry)
    return entry


@dataclass
class Ticket:
    """A normalized support ticket with triage and human review details."""

    id: str
    customer_name: str
    message: str
    status: str
    created_at: str
    category: str = "unclassified"
    urgency: str = "normal"
    suggested_category: str = "unclassified"
    suggested_urgency: str = "normal"
    review_status: str = "pending"
    reviewer_note: str = ""
    reviewed_at: str = ""
    resolution_status: str = "pending"
    approved_reply: str = ""
    resolution_note: str = ""
    resolution_reviewed_at: str = ""
    resolution_draft: str = ""
    resolution_action: str = ""
    domain: str = "ecommerce"
    status_updated_at: str = ""
    resolution_quality_score: int | None = None
    resolution_quality_checks: dict[str, bool] = field(default_factory=dict)
    owner_sub: str = ""
    zendesk_ticket_id: str = ""
    conversation: list[dict] = field(default_factory=list)
    reference_id: str = ""
    assigned_team: str = ""
    sla_due_at: str = ""
    first_response_at: str = ""

    def to_dict(self) -> dict:
        """Return this ticket as a plain dictionary."""
        return asdict(self)


def create_ticket(customer_name: str, message: str, domain: str = "ecommerce", owner_sub: str = "", reference_id: str = "") -> Ticket:
    """Validate customer input and create a new open ticket."""
    customer_name = customer_name.strip()
    message = message.strip()
    if not customer_name:
        raise ValueError("Customer name cannot be empty.")
    if not message:
        raise ValueError("Message cannot be empty.")
    if len(customer_name) > 120:
        raise ValueError("Customer name must be no longer than 120 characters.")
    if len(message) > 4000:
        raise ValueError("Message must be no longer than 4,000 characters.")
    if not isinstance(reference_id, str) or len(reference_id.strip()) > 100:
        raise ValueError("Reference ID must be a string no longer than 100 characters.")
    domain_config = get_domain(domain)
    if not isinstance(owner_sub, str):
        raise ValueError("owner_sub must be a string.")
    created_at = datetime.now(timezone.utc)
    ticket = Ticket(
        id=str(uuid4()), customer_name=customer_name, message=message,
        status="open", created_at=created_at.isoformat(), domain=domain,
        status_updated_at=created_at.isoformat(), owner_sub=owner_sub,
        reference_id=reference_id.strip(), assigned_team=domain_config.get("team", "Customer Support"),
    )
    return ticket


def triage_ticket(ticket: Ticket) -> Ticket:
    """Suggest a category and urgency using simple keyword rules."""
    text = ticket.message.lower()
    domain_keywords = {
        "food_delivery": {
            "missing_items": ("missing", "not included", "forgot"), "food_quality": ("cold", "spoiled", "allergy", "food safety"),
            "delivery": ("delivery", "late", "driver", "tracking"), "payments": ("charged", "charge", "refund", "payment"),
            "orders": ("order", "cancel", "restaurant"), "account": ("account", "password", "login"),
        },
        "edtech": {
            "course_access": ("course", "enrollment", "access", "locked out"), "classes": ("class", "lecture", "session", "schedule"),
            "content": ("lesson", "video", "assignment", "content"), "instructors": ("instructor", "teacher", "tutor"),
            "payments": ("charged", "charge", "refund", "subscription", "payment"), "account": ("account", "password", "login"),
        },
        "ecommerce": {
            "billing": ("charged", "charge", "refund", "billing", "invoice", "payment"),
            "product": ("damaged", "defective", "product", "broken"), "returns": ("return", "exchange", "send back"),
            "shipping": ("shipping", "delivery", "delivered", "tracking", "order", "arrived"),
            "technical": ("error", "bug", "crash", "not working"), "account": ("account", "password", "sign in", "login", "locked"),
        },
        "healthcare": {
            "appointments": ("appointment", "booking", "schedule", "cancel", "reschedule"),
            "prescriptions": ("prescription", "refill", "pharmacy", "medication"),
            "records": ("records", "medical history", "test result", "lab result"),
            "billing": ("charged", "charge", "bill", "insurance", "payment"),
            "portal_access": ("portal", "login", "password", "sign in", "account"),
        },
        "travel": {
            "bookings": ("booking", "reservation", "confirmation"),
            "itinerary_changes": ("change flight", "reschedule", "itinerary", "missed connection"),
            "cancellations": ("cancel", "cancellation", "refund"),
            "baggage": ("baggage", "luggage", "suitcase", "lost bag"),
            "payments": ("charged", "charge", "payment", "invoice"),
        },
        "telecom": {
            "connectivity": ("no service", "signal", "network", "internet", "dropped call", "sim swap"),
            "plans": ("plan", "data limit", "upgrade", "downgrade"),
            "devices": ("device", "phone", "router", "broken"),
            "billing": ("charged", "charge", "bill", "payment", "credit"),
            "account": ("account", "password", "login", "identity theft"),
        },
    }
    category = "general"
    category_keywords = domain_keywords[ticket.domain]
    for candidate, keywords in category_keywords.items():
        if any(keyword in text for keyword in keywords):
            category = candidate
            break
    urgent_keywords = ("urgent", "asap", "immediately", "safety", "fraud", "unable to access")
    critical_keywords = ("emergency", "chest pain", "can't breathe", "cannot breathe", "suicidal")
    urgency = "critical" if ticket.domain == "healthcare" and any(word in text for word in critical_keywords) else (
        "high" if any(word in text for word in urgent_keywords) else "normal"
    )
    ticket.category = ticket.suggested_category = category
    ticket.urgency = ticket.suggested_urgency = urgency
    created = datetime.fromisoformat(ticket.created_at)
    ticket.sla_due_at = (created + timedelta(hours=SLA_HOURS[urgency])).isoformat()
    ticket.assigned_team = get_domain(ticket.domain).get("category_teams", {}).get(
        category, get_domain(ticket.domain).get("team", "Customer Support")
    )
    return ticket


def review_triage(
    ticket: Ticket, *, category: str | None = None,
    urgency: str | None = None, note: str = "",
) -> Ticket:
    """Confirm or correct triage, preserving the original suggestions."""
    if ticket.suggested_category == "unclassified":
        raise ValueError("Run triage_ticket() before reviewing the ticket.")
    final_category = (category or ticket.category).strip().lower()
    final_urgency = (urgency or ticket.urgency).strip().lower()
    categories = set(get_domain(ticket.domain)["categories"])
    if final_category not in categories:
        raise ValueError(f"Category for {ticket.domain} must be one of: {', '.join(sorted(categories))}.")
    if final_urgency not in URGENCIES:
        raise ValueError(f"Urgency must be one of: {', '.join(sorted(URGENCIES))}.")
    ticket.category = final_category
    ticket.urgency = final_urgency
    ticket.reviewer_note = note.strip()
    ticket.reviewed_at = utc_now()
    ticket.review_status = (
        "corrected" if final_category != ticket.suggested_category
        or final_urgency != ticket.suggested_urgency else "confirmed"
    )
    created = datetime.fromisoformat(ticket.created_at)
    ticket.sla_due_at = (created + timedelta(hours=SLA_HOURS[final_urgency])).isoformat()
    ticket.assigned_team = get_domain(ticket.domain).get("category_teams", {}).get(
        final_category, get_domain(ticket.domain).get("team", "Customer Support")
    )
    return ticket
