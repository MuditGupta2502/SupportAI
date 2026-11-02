"""HTTP API and browser UI for the SupportAI portfolio application.

Run with: python -m supportai.api
Use localhost for development; public deployments require password or OIDC auth over HTTPS.
"""

import ipaddress
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from supportai.auth import AuthManager, AuthenticationError
from supportai.zendesk import ZendeskError, create_internal_ticket, zendesk_configured
from supportai.domains import public_domains
from supportai.feedback import build_feedback_report
from supportai.knowledge import knowledge_backend_status
from supportai.workflow import run_resolution_workflow
from supportai.storage import load_tickets, save_tickets
from supportai.tickets import STATUS_TRANSITIONS, TICKET_STATUSES, add_conversation_entry, create_ticket, review_triage, triage_ticket, utc_now
from supportai.policy import review_submitted_reply

HOST = os.getenv("SUPPORTAI_HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
MAX_BODY_BYTES = 64_000
INDEX_FILE = Path(__file__).with_name("static") / "index.html"
AUTH = AuthManager()
ZENDESK_SYNC_LOCK = threading.Lock()
API_WRITE_LOCK = threading.RLock()


class RequestRateLimiter:
    """Small per-process sliding-window limits for this single-instance demo host."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}

    def allow(self, key: str, *, maximum: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            recent = [stamp for stamp in self._events.get(key, []) if now - stamp < window_seconds]
            if len(recent) >= maximum:
                self._events[key] = recent
                return False
            recent.append(now)
            self._events[key] = recent
            if len(self._events) > 10_000:
                self._events = {
                    item_key: [stamp for stamp in stamps if now - stamp < 3600]
                    for item_key, stamps in self._events.items()
                    if any(now - stamp < 3600 for stamp in stamps)
                }
            return True


RATE_LIMITER = RequestRateLimiter()


class SupportAIHandler(BaseHTTPRequestHandler):
    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if AUTH.cookie_secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )

    def _send_json(self, status: int, payload: dict | list, *, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, location: str, *, cookie: str = "", clear_flow: bool = False) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self._security_headers()
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if clear_flow:
            self.send_header("Set-Cookie", AUTH.clear_flow_cookie_header())
        self.end_headers()

    def _send_index(self) -> None:
        body = INDEX_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _current_user(self) -> dict | None:
        return AUTH.get_session(self.headers.get("Cookie", ""))

    def _client_ip(self) -> str:
        if os.getenv("SUPPORTAI_TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes"}:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return self.client_address[0]

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        return urlparse(origin).netloc.casefold() == self.headers.get("Host", "").casefold()

    def _require_admin(self, user: dict) -> bool:
        if user.get("role") == "admin":
            return True
        self._send_json(403, {"error": "This action is available to support staff only."})
        return False

    @staticmethod
    def _customer_ticket_view(ticket) -> dict:
        return {
            "id": ticket.id,
            "customer_name": ticket.customer_name,
            "message": ticket.message,
            "domain": ticket.domain,
            "category": ticket.category,
            "urgency": ticket.urgency,
            "status": ticket.status,
            "created_at": ticket.created_at,
            "status_updated_at": ticket.status_updated_at,
            "reference_id": ticket.reference_id,
            "assigned_team": ticket.assigned_team,
            "sla_due_at": ticket.sla_due_at,
            "first_response_at": ticket.first_response_at,
            "conversation": [entry for entry in ticket.conversation if entry.get("visibility") == "public"],
        }

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body must be between 1 byte and 64 KB.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Request body must contain valid JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        if path == "/":
            self._send_index()
            return
        if path == "/health":
            self._send_json(200, {
                "status": "ok",
                "service": "SupportAI",
                "authentication": AUTH.mode,
                "knowledge": knowledge_backend_status(),
            })
            return
        if path == "/auth/config":
            self._send_json(200, {"mode": AUTH.mode, "zendesk_configured": zendesk_configured()})
            return
        if path == "/auth/me":
            user = self._current_user()
            if user is None:
                self._send_json(401, {"error": "Sign-in required."})
            else:
                self._send_json(200, user)
            return
        if path == "/auth/login":
            if AUTH.mode != "oidc":
                self._send_json(400, {"error": "Use the local customer or admin login form."})
                return
            try:
                location, flow_cookie = AUTH.begin_oidc_login()
                self._send_redirect(location, cookie=flow_cookie)
            except AuthenticationError as error:
                self._send_json(502, {"error": str(error)})
            return
        if path == "/auth/callback":
            query = parse_qs(parsed.query)
            if query.get("error"):
                self._send_json(401, {"error": "Sign-in was cancelled or denied by the identity provider."}, headers={"Set-Cookie": AUTH.clear_flow_cookie_header()})
                return
            try:
                state = query.get("state", [""])[0]
                code = query.get("code", [""])[0]
                if not state or not code:
                    raise AuthenticationError("The identity provider callback is incomplete.")
                flow_cookie = AUTH.flow_cookie_value(self.headers.get("Cookie", ""))
                session_id, _user = AUTH.finish_oidc_login(state, code, flow_cookie)
                self._send_redirect("/", cookie=AUTH.set_cookie_header(session_id), clear_flow=True)
            except AuthenticationError as error:
                self._send_json(401, {"error": str(error)}, headers={"Set-Cookie": AUTH.clear_flow_cookie_header()})
            return
        if path == "/domains":
            user = self._current_user()
            if user is None:
                self._send_json(401, {"error": "Sign-in required."})
            else:
                self._send_json(200, public_domains(include_policies=user["role"] == "admin"))
            return

        user = self._current_user()
        if user is None:
            self._send_json(401, {"error": "Sign-in required."})
            return
        tickets = load_tickets()
        if path == "/feedback":
            if not self._require_admin(user):
                return
            domain = parse_qs(parsed.query).get("domain", [""])[0]
            if domain and domain != "all":
                valid_domains = {item["id"] for item in public_domains()}
                if domain not in valid_domains:
                    self._send_json(400, {"error": f"Unknown domain '{domain}'."})
                    return
                tickets = [ticket for ticket in tickets if ticket.domain == domain]
            report = build_feedback_report(tickets)
            report["category_corrections"] = dict(report["category_corrections"])
            report["urgency_corrections"] = dict(report["urgency_corrections"])
            self._send_json(200, report)
            return
        if path == "/tickets":
            domain = parse_qs(parsed.query).get("domain", [""])[0]
            if domain and domain != "all":
                try:
                    public_domains_by_id = {item["id"] for item in public_domains()}
                    if domain not in public_domains_by_id:
                        raise ValueError(f"Unknown domain '{domain}'.")
                except ValueError as error:
                    self._send_json(400, {"error": str(error)})
                    return
                tickets = [ticket for ticket in tickets if ticket.domain == domain]
            if user["role"] != "admin":
                tickets = [ticket for ticket in tickets if ticket.owner_sub == user["sub"]]
                ticket_views = [self._customer_ticket_view(ticket) for ticket in tickets]
            else:
                ticket_views = [ticket.to_dict() for ticket in tickets]
            self._send_json(200, ticket_views)
            return

        parts = path.strip("/").split("/")
        if len(parts) in {2, 3} and parts[0] == "tickets":
            ticket = self._find_ticket(tickets, parts[1])
            if ticket is None:
                self._send_json(404, {"error": "Ticket not found or ID prefix is ambiguous."})
                return
            if user["role"] != "admin" and ticket.owner_sub != user["sub"]:
                self._send_json(404, {"error": "Ticket not found."})
                return
            if len(parts) == 2:
                self._send_json(200, ticket.to_dict() if user["role"] == "admin" else self._customer_ticket_view(ticket))
                return
            if parts[2] == "resolution":
                if not self._require_admin(user):
                    return
                if not RATE_LIMITER.allow(f"resolution:{user['sub']}", maximum=20, window_seconds=3600):
                    self._send_json(429, {"error": "Draft generation limit reached. Try again later."}, headers={"Retry-After": "3600"})
                    return
                if not RATE_LIMITER.allow(f"resolution-ip:{self._client_ip()}", maximum=20, window_seconds=3600):
                    self._send_json(429, {"error": "Draft generation limit reached for this network. Try again later."}, headers={"Retry-After": "3600"})
                    return
                workflow = run_resolution_workflow(ticket)
                plan = workflow["plan"]
                review = workflow["policy_review"]
                quality = workflow["quality"]
                self._send_json(200, {
                    "plan": plan.__dict__,
                    "policy_review": {
                        "passed": review.passed,
                        "findings": review.findings,
                        "blocking_findings": review.blocking_findings,
                        "escalation_recommended": review.escalation_recommended,
                        "escalation_reasons": review.escalation_reasons,
                    },
                    "quality": quality.to_dict(),
                    "agent_trace": workflow.get("agent_trace", []),
                })
                return

        self._send_json(404, {"error": "Route not found."})

    @staticmethod
    def _find_ticket(tickets, ticket_id: str):
        matches = [ticket for ticket in tickets if ticket.id.startswith(ticket_id)]
        return matches[0] if len(matches) == 1 else None

    def do_POST(self) -> None:
        # Local JSON storage is protected from concurrent read-modify-write requests.
        with API_WRITE_LOCK:
            self._do_post_serialized()

    def _do_post_serialized(self) -> None:
        path = unquote(urlparse(self.path).path).rstrip("/") or "/"
        parts = path.strip("/").split("/")

        if not self._origin_allowed():
            self._send_json(403, {"error": "Request origin is not allowed."})
            return

        user = self._current_user()
        if path in {"/auth/login", "/auth/signup"}:
            if AUTH.mode not in {"dev", "password"}:
                self._send_json(400, {"error": "Local password accounts are disabled. Use organization SSO."})
                return
            if path == "/auth/login" and not RATE_LIMITER.allow(
                f"login-ip:{self._client_ip()}", maximum=30, window_seconds=900
            ):
                self._send_json(429, {"error": "Too many sign-in attempts from this network. Try again later."}, headers={"Retry-After": "900"})
                return
            if path == "/auth/signup" and not RATE_LIMITER.allow(
                f"signup:{self._client_ip()}", maximum=5, window_seconds=3600
            ):
                self._send_json(429, {"error": "Too many signup attempts from this network. Try again later."}, headers={"Retry-After": "3600"})
                return
            try:
                payload = self._read_json()
                email = payload.get("email", "")
                password = payload.get("password", "")
                if path == "/auth/signup":
                    name = payload.get("name", "")
                    confirmation = payload.get("password_confirm", "")
                    if not all(isinstance(value, str) for value in (email, name, password, confirmation)):
                        raise ValueError("Name, email, password, and password confirmation must be strings.")
                    if password != confirmation:
                        raise ValueError("Passwords do not match.")
                    session_id, account = AUTH.register_customer(email, name, password)
                else:
                    account_type = payload.get("account_type", "")
                    if not isinstance(email, str) or not isinstance(password, str) or not isinstance(account_type, str):
                        raise ValueError("Email, password, and account type must be strings.")
                    session_id, account = AUTH.login_local(
                        email, password, expected_role=account_type, client_ip=self._client_ip()
                    )
                self._send_json(200, {"status": "signed_in", "role": account["role"]},
                                headers={"Set-Cookie": AUTH.set_cookie_header(session_id)})
            except (ValueError, AuthenticationError) as error:
                self._send_json(400, {"error": str(error)})
            return
        if path == "/auth/logout":
            if user is None:
                self._send_json(401, {"error": "Sign-in required."})
                return
            if not AUTH.csrf_valid(self.headers.get("Cookie", ""), self.headers.get("X-CSRF-Token", "")):
                self._send_json(403, {"error": "CSRF validation failed. Refresh and try again."})
                return
            AUTH.logout(self.headers.get("Cookie", ""))
            self._send_json(200, {"status": "signed_out"}, headers={"Set-Cookie": AUTH.clear_cookie_header()})
            return
        if user is None:
            self._send_json(401, {"error": "Sign-in required."})
            return
        if not AUTH.csrf_valid(self.headers.get("Cookie", ""), self.headers.get("X-CSRF-Token", "")):
            self._send_json(403, {"error": "CSRF validation failed. Refresh and try again."})
            return

        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "resolution-review":
            if not self._require_admin(user):
                return
            self._save_resolution_review(parts[1])
            return
        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "triage-review":
            if not self._require_admin(user):
                return
            self._save_triage_review(parts[1])
            return
        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "status":
            if not self._require_admin(user):
                return
            self._save_ticket_status(parts[1], user)
            return
        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "messages":
            if not RATE_LIMITER.allow(f"messages:{user['sub']}", maximum=60, window_seconds=3600):
                self._send_json(429, {"error": "Comment limit reached. Try again later."}, headers={"Retry-After": "3600"})
                return
            if not RATE_LIMITER.allow(f"messages-ip:{self._client_ip()}", maximum=180, window_seconds=3600):
                self._send_json(429, {"error": "Comment limit reached for this network. Try again later."}, headers={"Retry-After": "3600"})
                return
            self._save_ticket_message(parts[1], user)
            return
        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "zendesk":
            if not self._require_admin(user):
                return
            self._sync_zendesk_ticket(parts[1])
            return
        if path != "/tickets":
            self._send_json(404, {"error": "Route not found."})
            return

        if not RATE_LIMITER.allow(f"ticket-create:{user['sub']}", maximum=20, window_seconds=3600):
            self._send_json(429, {"error": "Ticket creation limit reached. Try again later."}, headers={"Retry-After": "3600"})
            return
        if not RATE_LIMITER.allow(f"ticket-create-ip:{self._client_ip()}", maximum=40, window_seconds=3600):
            self._send_json(429, {"error": "Ticket creation limit reached for this network. Try again later."}, headers={"Retry-After": "3600"})
            return

        try:
            payload = self._read_json()
            name = payload.get("customer_name")
            message = payload.get("message")
            domain = payload.get("domain", "ecommerce")
            owner_sub = user["sub"] if user["role"] == "customer" else payload.get("owner_sub", "")
            reference_id = payload.get("reference_id", "")
            if user["role"] == "customer":
                name = user["name"]
            if not isinstance(name, str) or not isinstance(message, str):
                raise ValueError("customer_name and message must both be strings.")
            if not isinstance(domain, str):
                raise ValueError("domain must be a string.")
            if not isinstance(owner_sub, str):
                raise ValueError("owner_sub must be a string.")
            if not isinstance(reference_id, str):
                raise ValueError("reference_id must be a string.")
            ticket = triage_ticket(create_ticket(name, message, domain=domain, owner_sub=owner_sub, reference_id=reference_id))
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return

        tickets = load_tickets()
        tickets.append(ticket)
        save_tickets(tickets)
        self._send_json(201, ticket.to_dict() if user["role"] == "admin" else self._customer_ticket_view(ticket))

    def _save_triage_review(self, ticket_id: str) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return

        category = payload.get("category")
        urgency = payload.get("urgency")
        note = payload.get("note", "")
        if not all(isinstance(value, str) for value in (category, urgency, note)):
            self._send_json(400, {"error": "category, urgency, and note must be strings."})
            return

        tickets = load_tickets()
        ticket = self._find_ticket(tickets, ticket_id)
        if ticket is None:
            self._send_json(404, {"error": "Ticket not found or ID prefix is ambiguous."})
            return
        try:
            review_triage(ticket, category=category, urgency=urgency, note=note)
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        save_tickets(tickets)
        self._send_json(200, ticket.to_dict())

    def _save_resolution_review(self, ticket_id: str) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return

        action = payload.get("action")
        if action not in {"approve", "edit", "reject"}:
            self._send_json(400, {"error": "action must be approve, edit, or reject."})
            return

        tickets = load_tickets()
        ticket = self._find_ticket(tickets, ticket_id)
        if ticket is None:
            self._send_json(404, {"error": "Ticket not found or ID prefix is ambiguous."})
            return

        draft_reply = payload.get("draft_reply", "")
        if not isinstance(draft_reply, str):
            self._send_json(400, {"error": "draft_reply must be a string."})
            return
        if len(draft_reply) > 10_000:
            self._send_json(400, {"error": "draft_reply must be no longer than 10,000 characters."})
            return
        quality = payload.get("quality")
        if quality is not None:
            if not isinstance(quality, dict):
                self._send_json(400, {"error": "quality must be an object."})
                return
            score = quality.get("score")
            checks = quality.get("checks", {})
            if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
                self._send_json(400, {"error": "quality.score must be an integer from 0 to 100."})
                return
            if not isinstance(checks, dict) or any(not isinstance(value, bool) for value in checks.values()):
                self._send_json(400, {"error": "quality.checks must be an object containing boolean values."})
                return
            ticket.resolution_quality_score = score
            ticket.resolution_quality_checks = checks
        ticket.resolution_draft = draft_reply.strip()
        ticket.resolution_action = action
        if action in {"approve", "edit"}:
            reply = payload.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                self._send_json(400, {"error": "An approved reply must not be empty."})
                return
            policy_review = review_submitted_reply(ticket, reply.strip())
            if policy_review.blocking_findings:
                self._send_json(409, {
                    "error": "Reply blocked by policy. Edit it before approval.",
                    "blocking_findings": policy_review.blocking_findings,
                })
                return
            ticket.approved_reply = reply.strip()
            ticket.resolution_status = "approved"
            ticket.resolution_note = "Edited and approved by support agent." if action == "edit" else "Approved by support agent."
        else:
            ticket.approved_reply = ""
            ticket.resolution_status = "rejected"
            note = payload.get("note", "")
            if not isinstance(note, str):
                self._send_json(400, {"error": "note must be a string."})
                return
            ticket.resolution_note = note.strip()

        ticket.resolution_reviewed_at = utc_now()
        save_tickets(tickets)
        self._send_json(200, ticket.to_dict())

    def _save_ticket_status(self, ticket_id: str, user: dict) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return

        status = payload.get("status")
        if not isinstance(status, str) or status not in TICKET_STATUSES:
            choices = ", ".join(sorted(TICKET_STATUSES))
            self._send_json(400, {"error": f"status must be one of: {choices}."})
            return

        tickets = load_tickets()
        ticket = self._find_ticket(tickets, ticket_id)
        if ticket is None:
            self._send_json(404, {"error": "Ticket not found or ID prefix is ambiguous."})
            return
        comment = payload.get("comment", "")
        visibility = payload.get("visibility", "public")
        if not isinstance(comment, str) or not isinstance(visibility, str):
            self._send_json(400, {"error": "comment and visibility must be strings."})
            return
        if visibility not in {"public", "internal"}:
            self._send_json(400, {"error": "visibility must be public or internal."})
            return
        old_status = ticket.status
        if status != old_status and status not in STATUS_TRANSITIONS.get(old_status, set()):
            allowed = ", ".join(sorted(STATUS_TRANSITIONS.get(old_status, set()))) or "none"
            self._send_json(409, {"error": f"Cannot move a {old_status.replace('_', ' ')} ticket to {status.replace('_', ' ')}. Allowed next statuses: {allowed}."})
            return
        if old_status != status and not comment.strip():
            self._send_json(400, {"error": "Add a customer update or internal note when changing ticket status."})
            return
        ticket.status = status
        ticket.status_updated_at = utc_now()
        if old_status != status or comment.strip():
            body = comment.strip() or f"Ticket status remains {status.replace('_', ' ')}."
            try:
                add_conversation_entry(ticket, author=user["name"], role="admin", body=body,
                                       visibility=visibility, kind="status")
            except ValueError as error:
                self._send_json(400, {"error": str(error)})
                return
        if not ticket.first_response_at and visibility == "public" and (old_status != status or comment.strip()):
            ticket.first_response_at = utc_now()
        save_tickets(tickets)
        self._send_json(200, ticket.to_dict())

    def _save_ticket_message(self, ticket_id: str, user: dict) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        body = payload.get("message")
        visibility = payload.get("visibility", "public")
        if not isinstance(body, str) or not isinstance(visibility, str):
            self._send_json(400, {"error": "message and visibility must be strings."})
            return
        if user["role"] == "customer":
            visibility = "public"
        elif visibility not in {"public", "internal"}:
            self._send_json(400, {"error": "visibility must be public or internal."})
            return
        tickets = load_tickets()
        ticket = self._find_ticket(tickets, ticket_id)
        if ticket is None or (user["role"] != "admin" and ticket.owner_sub != user["sub"]):
            self._send_json(404, {"error": "Ticket not found."})
            return
        try:
            if user["role"] == "customer" and ticket.status in {"resolved", "closed", "waiting_on_customer"}:
                prior_status = ticket.status
                ticket.status = "in_progress" if prior_status == "waiting_on_customer" else "open"
                ticket.status_updated_at = utc_now()
                add_conversation_entry(ticket, author="SupportAI", role="system",
                                       body=f"The customer replied, so this ticket moved from {prior_status.replace('_', ' ')} to {ticket.status.replace('_', ' ')}.",
                                       visibility="public", kind="status")
            add_conversation_entry(ticket, author=user["name"], role=user["role"], body=body,
                                   visibility=visibility, kind="message")
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        if user["role"] == "admin" and visibility == "public" and not ticket.first_response_at:
            ticket.first_response_at = utc_now()
        save_tickets(tickets)
        result = ticket.to_dict() if user["role"] == "admin" else self._customer_ticket_view(ticket)
        self._send_json(201, result)

    def _sync_zendesk_ticket(self, ticket_id: str) -> None:
        # The server uses threads; serialize the check/create/store sequence to avoid
        # duplicate Zendesk tickets from two near-simultaneous clicks in this process.
        with ZENDESK_SYNC_LOCK:
            self._sync_zendesk_ticket_locked(ticket_id)

    def _sync_zendesk_ticket_locked(self, ticket_id: str) -> None:
        tickets = load_tickets()
        ticket = self._find_ticket(tickets, ticket_id)
        if ticket is None:
            self._send_json(404, {"error": "Ticket not found or ID prefix is ambiguous."})
            return
        if ticket.resolution_status != "approved" or not ticket.approved_reply:
            self._send_json(409, {"error": "Approve a resolution draft before creating the Zendesk internal ticket."})
            return
        if ticket.zendesk_ticket_id:
            self._send_json(200, {"zendesk_ticket_id": ticket.zendesk_ticket_id, "status": "already_synced"})
            return
        try:
            external_id = create_internal_ticket(ticket)
        except ZendeskError as error:
            self._send_json(502, {"error": str(error)})
            return
        ticket.zendesk_ticket_id = str(external_id)
        save_tickets(tickets)
        self._send_json(201, {"zendesk_ticket_id": ticket.zendesk_ticket_id, "status": "created"})

    def log_message(self, format: str, *args) -> None:
        """Log request paths without OAuth codes, session tokens, or query data."""
        parts = self.requestline.split()
        method = parts[0] if parts else "?"
        raw_path = parts[1] if len(parts) > 1 else "/"
        safe_path = urlparse(raw_path).path
        status = str(args[1]) if len(args) > 1 else "?"
        print(f"{self.address_string()} - {method} {safe_path} {status}")


def main() -> None:
    if AUTH.mode == "dev" and HOST not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Development auth is only allowed on localhost. Use password or OIDC auth before binding publicly.")
    if AUTH.mode == "password" and HOST not in {"127.0.0.1", "localhost"} and not AUTH.cookie_secure:
        raise RuntimeError("Public password authentication requires SUPPORTAI_COOKIE_SECURE=true behind HTTPS.")
    server = ThreadingHTTPServer((HOST, PORT), SupportAIHandler)
    print(f"SupportAI is running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SupportAI...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
