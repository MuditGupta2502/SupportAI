"""Command-line interface for the SupportAI ticket prototype."""

import argparse

from supportai.feedback import build_feedback_report
from supportai.knowledge import search_articles
from supportai.policy import review_plan
from supportai.resolution import create_resolution_plan
from supportai.storage import load_tickets, save_tickets
from supportai.tickets import create_ticket, review_triage, triage_ticket, utc_now


def find_ticket(tickets, ticket_id: str):
    matches = [ticket for ticket in tickets if ticket.id.startswith(ticket_id)]
    return matches[0] if len(matches) == 1 else None


def print_results(results) -> None:
    if not results:
        print("No matching articles found. Try different keywords.")
        return
    for result in results:
        print(f"[{result.score:.0%}] {result.title} ({result.category})")
        print(f"Source: {result.source} | Article: {result.article_id}")
        print(result.content)
        print()


def print_feedback(tickets) -> None:
    report = build_feedback_report(tickets)
    print("SupportAI feedback summary")
    print(f"Tickets: {report['total_tickets']}")
    print(f"Triage reviewed: {report['triage_reviewed']} | corrected: {report['triage_corrected']} | pending: {report['triage_pending']}")
    category_accuracy = report["category_accuracy"]
    urgency_accuracy = report["urgency_accuracy"]
    print(f"Category suggestion agreement: {category_accuracy:.0%}" if category_accuracy is not None else "Category suggestion agreement: no reviewed data yet")
    print(f"Urgency suggestion agreement: {urgency_accuracy:.0%}" if urgency_accuracy is not None else "Urgency suggestion agreement: no reviewed data yet")
    print("Category corrections:")
    for change, count in report["category_corrections"].most_common():
        print(f"- {change}: {count}")
    if not report["category_corrections"]:
        print("- None recorded")
    print("Urgency corrections:")
    for change, count in report["urgency_corrections"].most_common():
        print(f"- {change}: {count}")
    if not report["urgency_corrections"]:
        print("- None recorded")
    print("Resolution reviews: "
          f"{report['resolution_approved']} approved, "
          f"{report['resolution_rejected']} rejected, "
          f"{report['resolution_pending']} pending")
    print("Draft feedback: "
          f"{report['resolution_draft_feedback']} reviewed, "
          f"{report['resolution_unchanged']} approved unchanged, "
          f"{report['resolution_edited']} edited, "
          f"{report['resolution_rejected_with_draft']} rejected")


def print_plan(plan) -> None:
    policy_review = review_plan(plan)
    print(f"Resolution plan for {plan.customer_name} (ticket {plan.ticket_id[:8]})")
    print(f"Issue: {plan.issue}")
    print("Status: DRAFT - human approval required")
    print("\nSuggested actions:")
    for action in plan.actions:
        print(f"- {action}")
    print("\nInformation to collect:")
    if plan.questions:
        for question in plan.questions:
            print(f"- {question}")
    else:
        print("- No specific questions found in the matching guidance.")
    print(f"\nDraft customer reply ({plan.reply_source}):")
    print(plan.customer_reply)
    if plan.reply_warning:
        print(f"Draft note: {plan.reply_warning}")
    print("\nSources:")
    for source in plan.sources or ["No matching support articles."]:
        print(f"- {source}")
    print("\nPolicy review:")
    if policy_review.passed:
        print("- No configured policy rules were triggered.")
    else:
        for finding in policy_review.findings:
            print(f"- REVIEW: {finding}")
    return policy_review


def review_resolution(ticket, draft_reply: str) -> None:
    """Interactively approve, edit, or reject a draft; never sends it."""
    while True:
        choice = input("\nAction: [a]pprove, [e]dit reply, [r]eject, [q]uit without saving? ").strip().lower()
        if choice in {"a", "approve"}:
            ticket.resolution_draft = draft_reply
            ticket.resolution_action = "approve"
            ticket.approved_reply = draft_reply
            ticket.resolution_status = "approved"
            ticket.resolution_note = "Approved as drafted by support agent."
            break
        if choice in {"e", "edit"}:
            ticket.resolution_draft = draft_reply
            ticket.resolution_action = "edit"
            edited_reply = input("Enter the final customer reply: ").strip()
            if not edited_reply:
                print("Reply cannot be empty. No changes saved.")
                continue
            ticket.approved_reply = edited_reply
            ticket.resolution_status = "approved"
            ticket.resolution_note = "Edited and approved by support agent."
            break
        if choice in {"r", "reject"}:
            ticket.resolution_draft = draft_reply
            ticket.resolution_action = "reject"
            ticket.approved_reply = ""
            ticket.resolution_status = "rejected"
            ticket.resolution_note = input("Why are you rejecting this draft? ").strip()
            break
        if choice in {"q", "quit"}:
            print("No resolution review saved.")
            return
        print("Choose a, e, r, or q.")

    ticket.resolution_reviewed_at = utc_now()
    print(f"Resolution marked {ticket.resolution_status}. No message was sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create, review, and search support tickets.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="List saved tickets")
    commands.add_parser("feedback", help="Summarize human triage corrections and resolution reviews")

    create_parser = commands.add_parser("create", help="Create and triage a ticket")
    create_parser.add_argument("--name", required=True, help="Customer name")
    create_parser.add_argument("--message", required=True, help="Customer's message")
    create_parser.add_argument("--domain", default="ecommerce", choices=("food_delivery", "edtech", "ecommerce", "healthcare", "travel", "telecom"), help="Support domain (default: ecommerce)")

    review_parser = commands.add_parser("review", help="Confirm or correct a ticket's triage")
    review_parser.add_argument("ticket_id", help="Ticket ID prefix shown by the list command")
    review_parser.add_argument("--category", help="billing, shipping, technical, account, or general")
    review_parser.add_argument("--urgency", help="low, normal, high, or critical")
    review_parser.add_argument("--note", default="", help="Reason for the review")

    search_parser = commands.add_parser("search", help="Search the support knowledge base")
    search_parser.add_argument("query", help="Words describing the customer issue")
    search_parser.add_argument("--category", help="Optional category filter")
    search_parser.add_argument("--domain", choices=("food_delivery", "edtech", "ecommerce", "healthcare", "travel", "telecom"), help="Optional domain filter")
    search_parser.add_argument("--limit", type=int, default=3, help="Maximum number of results (default: 3)")

    retrieve_parser = commands.add_parser("retrieve", help="Find knowledge articles for a saved ticket")
    retrieve_parser.add_argument("ticket_id", help="Ticket ID prefix shown by the list command")
    retrieve_parser.add_argument("--limit", type=int, default=3, help="Maximum number of results (default: 3)")

    resolve_parser = commands.add_parser("resolve", help="Draft and review a plan for a saved ticket")
    resolve_parser.add_argument("ticket_id", help="Ticket ID prefix shown by the list command")

    args = parser.parse_args()
    tickets = load_tickets()

    if args.command == "feedback":
        print_feedback(tickets)
        return

    if args.command == "list":
        if not tickets:
            print("No tickets saved yet. Create one with the create command.")
            return
        for ticket in tickets:
            print(
                f"{ticket.id[:8]} | {ticket.domain:13} | {ticket.status:6} | {ticket.category:14} | "
                f"{ticket.urgency:8} | resolution: {ticket.resolution_status:8} | "
                f"{ticket.customer_name}: {ticket.message}"
            )
        return

    if args.command == "create":
        ticket = triage_ticket(create_ticket(args.name, args.message, domain=args.domain))
        tickets.append(ticket)
        save_tickets(tickets)
        print(f"Saved ticket {ticket.id}")
        print(f"Domain: {ticket.domain}; suggested category: {ticket.category}; urgency: {ticket.urgency}")
        return

    if args.command == "search":
        print_results(search_articles(args.query, domain=args.domain, category=args.category, limit=args.limit))
        return

    if args.command in {"retrieve", "resolve"}:
        ticket = find_ticket(tickets, args.ticket_id)
        if ticket is None:
            print(f"No unique ticket found matching '{args.ticket_id}'.")
            return
        if args.command == "retrieve":
            print_results(search_articles(ticket.message, domain=ticket.domain, category=ticket.category, limit=args.limit))
        else:
            plan = create_resolution_plan(ticket)
            print_plan(plan)
            review_resolution(ticket, plan.customer_reply)
            if ticket.resolution_status != "pending":
                save_tickets(tickets)
        return

    ticket = find_ticket(tickets, args.ticket_id)
    if ticket is None:
        print(f"No unique ticket found matching '{args.ticket_id}'.")
        return

    review_triage(ticket, category=args.category, urgency=args.urgency, note=args.note)
    save_tickets(tickets)
    print(f"Ticket {ticket.id[:8]} {ticket.review_status}: {ticket.category}, {ticket.urgency}")


if __name__ == "__main__":
    main()
