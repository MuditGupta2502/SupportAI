"""Lightweight input and output checks around external LLM calls."""

import re

from supportai.knowledge import SearchResult
from supportai.tickets import Ticket


_INJECTION_PATTERNS = (
    ("instruction override", re.compile(
        r"\b(?:ignore|disregard|forget|override)\b.{0,50}\b(?:previous|prior|all|system|developer)\b.{0,30}\b(?:instructions?|rules?|prompts?)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("hidden prompt extraction", re.compile(
        r"\b(?:reveal|show|print|repeat|display|leak|expose)\b.{0,60}\b(?:system|developer|hidden|internal)\b.{0,30}\b(?:prompt|instructions?|message)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("safety bypass", re.compile(
        r"\b(?:bypass|disable|evade|jailbreak)\b.{0,40}\b(?:safety|guardrails?|polic(?:y|ies)|filters?|restrictions?)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("role impersonation", re.compile(
        r"\b(?:act|behave|respond)\s+as\s+(?:the\s+)?(?:system|developer|admin(?:istrator)?)\b",
        re.IGNORECASE,
    )),
)

_OUTPUT_LEAK_PATTERNS = (
    ("internal prompt disclosure", re.compile(r"\b(?:system prompt|developer instructions|hidden instructions|chain of thought)\b", re.IGNORECASE)),
    ("credential-like value", re.compile(r"\b(?:AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9_-]{20,})\b")),
)
_SENSITIVE_INPUT_PATTERNS = (
    ("government identity number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("password or one-time code", re.compile(
        r"\b(?:password|passcode|one[- ]time code|verification code|otp)\s*(?:is|:|=)\s*\S+",
        re.IGNORECASE,
    )),
)


def contains_sensitive_input(message: str) -> str:
    """Detect common secrets before sending a customer message to an external model."""
    for label, pattern in _SENSITIVE_INPUT_PATTERNS:
        if pattern.search(message):
            return label
    for candidate in re.findall(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", message):
        digits = re.sub(r"\D", "", candidate)
        if len(digits) < 13 or len(digits) > 19:
            continue
        total = 0
        for index, char in enumerate(reversed(digits)):
            value = int(char)
            if index % 2:
                value *= 2
                if value > 9:
                    value -= 9
            total += value
        if total % 10 == 0:
            return "payment card number"
    return ""


def find_prompt_injection(ticket: Ticket, sources: list[SearchResult]) -> list[str]:
    """Find instruction-like attacks in customer or retrieved text, treating them as data."""
    suspicious = []
    fields = [("customer message", ticket.message)]
    fields.extend((f"support article '{source.title}'", source.content) for source in sources)
    for field_name, content in fields:
        for label, pattern in _INJECTION_PATTERNS:
            if pattern.search(content):
                suspicious.append(f"{label} detected in {field_name}")
    return suspicious


def unsafe_model_output(reply: str) -> str:
    """Return a short reason if a draft appears to expose internal data."""
    for label, pattern in _OUTPUT_LEAK_PATTERNS:
        if pattern.search(reply):
            return label
    return ""
