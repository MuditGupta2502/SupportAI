"""Chroma vector index for domain-scoped support articles."""

import hashlib
import os
import threading
from pathlib import Path

from supportai.knowledge import load_articles
from supportai.data_paths import DATA_DIR


CHROMA_PATH = DATA_DIR / "chroma_db"
COLLECTION_NAME = "supportai_knowledge_v1"
_LOCK = threading.RLock()
_COLLECTION = None


def _get_collection():
    global _COLLECTION
    if _COLLECTION is not None:
        return _COLLECTION
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    _COLLECTION = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "purpose": "domain-scoped-support-knowledge"},
    )
    return _COLLECTION


def _article_document(article: dict) -> str:
    return f"{article['title']}\n{article['category']}\n{article['content']}"


def _sync_articles(collection) -> None:
    articles = load_articles()
    current = collection.get(include=["metadatas"])
    existing = {
        article_id: (metadata or {}).get("content_hash", "")
        for article_id, metadata in zip(current.get("ids", []), current.get("metadatas", []))
    }
    valid_ids = {article["id"] for article in articles}
    stale_ids = [article_id for article_id in existing if article_id not in valid_ids]
    if stale_ids:
        collection.delete(ids=stale_ids)

    changed = []
    for article in articles:
        document = _article_document(article)
        digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
        if existing.get(article["id"]) != digest:
            changed.append((article, document, digest))
    if changed:
        collection.upsert(
            ids=[article["id"] for article, _, _ in changed],
            documents=[document for _, document, _ in changed],
            metadatas=[
                {
                    "domain": article.get("domain", "ecommerce"),
                    "category": article["category"],
                    "title": article["title"],
                    "content": article["content"],
                    "source": article["source"],
                    "content_hash": digest,
                }
                for article, _, digest in changed
            ],
        )


def search_articles(query: str, *, domain: str | None, category: str | None, limit: int) -> list[dict]:
    """Search persistent Chroma vectors, syncing article edits before each query."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    with _LOCK:
        collection = _get_collection()
        _sync_articles(collection)
        count = collection.count()
        if not count:
            return []
        filters = []
        if domain:
            filters.append({"domain": {"$eq": domain}})
        if category:
            filters.append({"category": {"$eq": category.lower()}})
        where = filters[0] if len(filters) == 1 else {"$and": filters} if filters else None
        result = collection.query(
            query_texts=[query],
            n_results=min(count, max(limit * 3, limit)),
            where=where,
            include=["metadatas", "distances"],
        )

    rows = []
    ids = result.get("ids", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for article_id, metadata, distance in zip(ids, metadatas, distances):
        if not metadata:
            continue
        score = max(0.0, min(1.0, 1.0 - float(distance)))
        rows.append({
            "article_id": article_id,
            "title": metadata["title"],
            "category": metadata["category"],
            "content": metadata["content"],
            "source": metadata["source"],
            "score": score,
        })
    return sorted(rows, key=lambda row: row["score"], reverse=True)[:limit]
