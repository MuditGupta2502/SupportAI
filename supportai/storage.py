"""Ticket persistence in local JSON for development or Postgres for hosting."""

import json
import os
import threading
from pathlib import Path

from supportai.data_paths import DATA_DIR
from supportai.tickets import Ticket

TICKETS_FILE = DATA_DIR / "tickets.json"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def _postgres_connection():
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError("Install psycopg[binary] to use DATABASE_URL.") from error
    return psycopg.connect(DATABASE_URL, connect_timeout=10)


def _ensure_postgres_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with _postgres_connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS supportai_tickets ("
                "ticket_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload JSONB NOT NULL)"
            )
        _SCHEMA_READY = True


def load_tickets() -> list[Ticket]:
    """Load saved tickets from the configured durable store."""
    if DATABASE_URL:
        _ensure_postgres_schema()
        with _postgres_connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM supportai_tickets ORDER BY created_at, ticket_id"
            ).fetchall()
        return [Ticket(**(row[0] if isinstance(row[0], dict) else json.loads(row[0]))) for row in rows]

    if os.getenv("SUPPORTAI_REQUIRE_DATABASE", "").strip().lower() in {"1", "true", "yes"}:
        raise RuntimeError("DATABASE_URL is required; refusing to use ephemeral local ticket storage.")
    if not TICKETS_FILE.exists():
        return []
    with TICKETS_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return [Ticket(**item) for item in data]


def save_tickets(tickets: list[Ticket]) -> None:
    """Persist the ticket collection atomically to Postgres or local JSON."""
    if DATABASE_URL:
        _ensure_postgres_schema()
        rows = [
            (ticket.id, ticket.created_at, json.dumps(ticket.to_dict(), ensure_ascii=False))
            for ticket in tickets
        ]
        with _postgres_connection() as connection:
            connection.execute("DELETE FROM supportai_tickets")
            if rows:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO supportai_tickets(ticket_id, created_at, payload) VALUES (%s, %s, %s::jsonb)",
                        rows,
                    )
        return

    if os.getenv("SUPPORTAI_REQUIRE_DATABASE", "").strip().lower() in {"1", "true", "yes"}:
        raise RuntimeError("DATABASE_URL is required; refusing to use ephemeral local ticket storage.")
    temporary_file = TICKETS_FILE.with_suffix(".tmp")
    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump([ticket.to_dict() for ticket in tickets], file, indent=2)
        file.write("\n")
    temporary_file.replace(TICKETS_FILE)
