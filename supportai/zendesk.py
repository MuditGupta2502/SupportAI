"""Explicit, admin-triggered Zendesk ticket creation using private notes only."""

import base64
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from supportai.tickets import Ticket


class ZendeskError(RuntimeError):
    """Safe-to-display Zendesk integration error without credential details."""


def zendesk_configured() -> bool:
    return all(os.getenv(name, "").strip() for name in (
        "SUPPORTAI_ZENDESK_SUBDOMAIN",
        "SUPPORTAI_ZENDESK_EMAIL",
        "SUPPORTAI_ZENDESK_API_TOKEN",
        "SUPPORTAI_ZENDESK_REQUESTER_ID",
    ))


def create_internal_ticket(ticket: Ticket) -> str:
    """Create a Zendesk ticket with a private note; never post a public reply."""
    if not zendesk_configured():
        raise ZendeskError("Zendesk is not configured. Set the integration values in .env and restart SupportAI.")
    subdomain = os.environ["SUPPORTAI_ZENDESK_SUBDOMAIN"].strip()
    if not re.fullmatch(r"[a-z0-9-]+", subdomain, re.IGNORECASE):
        raise ZendeskError("Zendesk subdomain configuration is invalid.")
    email = os.environ["SUPPORTAI_ZENDESK_EMAIL"].strip()
    token = os.environ["SUPPORTAI_ZENDESK_API_TOKEN"].strip()
    requester = os.environ["SUPPORTAI_ZENDESK_REQUESTER_ID"].strip()
    try:
        requester_id = int(requester)
        if requester_id <= 0:
            raise ValueError
    except ValueError as error:
        raise ZendeskError("Zendesk requester ID must be a positive integer.") from error

    note = (
        f"SupportAI ticket: {ticket.id}\nDomain: {ticket.domain}\n"
        f"Category: {ticket.category}\nUrgency: {ticket.urgency}\n"
        f"Customer issue:\n{ticket.message}\n\n"
        f"Agent-approved draft (private note only; not sent to customer):\n{ticket.approved_reply}"
    )
    payload = {
        "ticket": {
            "subject": f"[{ticket.domain}] Support request {ticket.id[:8]}: {ticket.category}",
            "requester_id": requester_id,
            "comment": {"body": note, "public": False},
            "priority": "urgent" if ticket.urgency == "critical" else ticket.urgency,
            "tags": ["supportai", ticket.domain, ticket.category],
        }
    }
    auth = base64.b64encode(f"{email}/token:{token}".encode("utf-8")).decode("ascii")
    request = Request(
        f"https://{subdomain}.zendesk.com/api/v2/tickets.json",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read(1_000_000))
    except HTTPError as error:
        # Do not reflect provider response bodies, which may contain user or tenant details.
        raise ZendeskError(f"Zendesk rejected the request (HTTP {error.code}). Check the integration configuration and permissions.") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ZendeskError("Zendesk could not be reached or returned an invalid response.") from error
    external_ticket = result.get("ticket", {}) if isinstance(result, dict) else {}
    external_id = external_ticket.get("id") if isinstance(external_ticket, dict) else None
    if not isinstance(external_id, (str, int)):
        raise ZendeskError("Zendesk response did not include a ticket ID.")
    return str(external_id)
