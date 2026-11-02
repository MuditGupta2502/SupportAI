"""Draft a human-reviewable plan from retrieved support guidance."""

import re
from dataclasses import dataclass

from supportai.tickets import Ticket


@dataclass
class ResolutionPlan:
    ticket_id: str
    domain: str
    domain_name: str
    customer_name: str
    issue: str
    actions: list[str]
    questions: list[str]
    customer_reply: str
    sources: list[str]
    reply_source: str = "template"
    reply_warning: str = ""
    needs_human_review: bool = True


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in text.split(". ") if part.strip()]


def _extract_actions(results):
    actions = []
    action_starts = (
        "if ", "ask ", "check ", "verify ", "collect ", "confirm ", "never ",
        "do not ", "escalate ", "for ", "suggest ",
    )
    for result in results:
        for sentence in _sentences(result.content):
            sentence = sentence.rstrip(".")
            if sentence.lower().startswith(action_starts) and sentence not in actions:
                actions.append(sentence)
    return actions


def _extract_questions(results):
    """Turn explicit information-collection guidance into customer questions."""
    questions = []
    seen = set()
    collection_patterns = (
        r"\bcollect\s+(?:the\s+)?(.+?)(?=\s+(?:,?\s+)?(?:and\s+)?(?:escalate|route|send|refer|contact)\b|[?.!]|$)",
        r"\bask\s+(?:the\s+)?customer\s+for\s+(.+?)(?=[?.!]|$)",
        r"\brequest\s+(?:the\s+)?(.+?)(?=[?.!]|$)",
    )
    for result in results:
        for sentence in _sentences(result.content):
            sentence = sentence.strip()
            if "?" in sentence:
                question = sentence
            else:
                match = next(
                    (found for pattern in collection_patterns
                     if (found := re.search(pattern, sentence, re.IGNORECASE))),
                    None,
                )
                if not match:
                    continue
                information = match.group(1).strip(" ,")
                if not information:
                    continue
                question = f"Could you provide {information}?"
            key = question.casefold()
            if key not in seen:
                seen.add(key)
                questions.append(question)
    return questions


def _template_reply(ticket: Ticket, results) -> str:
    if not results:
        return (
            f"Hi {ticket.customer_name}, thanks for contacting us. "
            "I am sorry you are having trouble. A support specialist will review your case and follow up."
        )

    title = results[0].title.lower()
    return (
        f"Hi {ticket.customer_name}, thanks for reaching out about {title}. "
        "I am sorry this has been frustrating. We are reviewing the details against our support guidance. "
        "Could you share the information requested below so we can confirm the right next step?"
    )


def create_resolution_plan(ticket: Ticket, *, limit: int = 3) -> ResolutionPlan:
    """Run the LangGraph resolution workflow and return its draft plan."""
    from supportai.workflow import run_resolution_workflow

    result = run_resolution_workflow(ticket, limit=limit)
    return result["plan"]
