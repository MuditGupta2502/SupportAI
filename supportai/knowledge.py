"""Simple local keyword search over support knowledge articles."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ARTICLES_FILE = Path(__file__).with_name("knowledge_base") / "articles.json"
_ACTIVE_BACKEND = "keyword"
_BACKEND_WARNING = ""
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "did", "do", "does",
    "for", "from", "had", "has", "have", "how", "i", "in", "is", "it", "its",
    "me", "my", "of", "on", "or", "our", "please", "so", "that", "the", "their",
    "them", "there", "they", "this", "to", "us", "was", "we", "were", "what",
    "when", "why", "will", "with", "you", "your", "very", "next", "time", "take",
    "care", "much", "not", "but", "if", "then", "than", "would", "could", "should",
}
SYNONYMS = {
    "logistics": "delivery", "shipment": "shipping", "shipments": "shipping",
    "delayed": "late", "delay": "late", "delays": "late",
}


@dataclass
class SearchResult:
    article_id: str
    title: str
    category: str
    content: str
    source: str
    score: float


def _terms(text: str) -> set[str]:
    """Return normalized lowercase terms, ignoring common filler words."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {SYNONYMS.get(word, word) for word in words if len(word) > 1 and word not in STOP_WORDS}


def load_articles() -> list[dict]:
    """Read the bundled articles from JSON."""
    with ARTICLES_FILE.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def _search_keywords(query: str, *, domain: str | None, category: str | None, limit: int) -> list[SearchResult]:
    query_terms = _terms(query)
    if not query_terms:
        return []
    if limit < 1:
        raise ValueError("limit must be at least 1")

    results = []
    for article in load_articles():
        article_domain = article.get("domain", "ecommerce")
        if domain and article_domain != domain:
            continue
        if category and article["category"] != category.lower():
            continue
        title_terms = _terms(article["title"])
        body_terms = _terms(f"{article['category']} {article['content']}")
        title_matches = query_terms & title_terms
        body_matches = (query_terms & body_terms) - title_matches
        if not title_matches and not body_matches:
            continue
        score = min(1.0, (3 * len(title_matches) + len(body_matches)) / (3 * min(4, len(query_terms))))
        results.append(SearchResult(
            article_id=article["id"],
            title=article["title"],
            category=article["category"],
            content=article["content"],
            source=article["source"],
            score=score,
        ))

    return sorted(results, key=lambda result: result.score, reverse=True)[:limit]


def search_articles(query: str, *, domain: str | None = None, category: str | None = None, limit: int = 3) -> list[SearchResult]:
    """Search Chroma vectors, with a deterministic keyword fallback."""
    global _ACTIVE_BACKEND, _BACKEND_WARNING
    if os.getenv("SUPPORTAI_KNOWLEDGE_MODE", "vector").strip().lower() != "keyword":
        try:
            from supportai.vector_store import search_articles as vector_search

            results = vector_search(query, domain=domain, category=category, limit=limit)
            _ACTIVE_BACKEND = "chroma"
            _BACKEND_WARNING = ""
            return [SearchResult(**result) for result in results]
        except Exception as error:
            _ACTIVE_BACKEND = "keyword"
            _BACKEND_WARNING = f"Chroma unavailable ({type(error).__name__}); using keyword search."
    else:
        _ACTIVE_BACKEND = "keyword"
        _BACKEND_WARNING = "Keyword search was selected by SUPPORTAI_KNOWLEDGE_MODE."
    return _search_keywords(query, domain=domain, category=category, limit=limit)


def knowledge_backend_status() -> dict:
    """Describe the selected and last active knowledge retrieval backend."""
    configured = os.getenv("SUPPORTAI_KNOWLEDGE_MODE", "vector").strip().lower()
    return {"configured": configured, "active": _ACTIVE_BACKEND, "warning": _BACKEND_WARNING}
