"""Escalated tickets awaiting a human decision, plus the decisions themselves.

Every Approve / Reject is stored with the bot's result, which makes it new labelled data:
`export_feedback()` turns it into eval rows, so the test set grows from real traffic.

Backend: Postgres when DATABASE_URL is set (deployed on Vercel with Neon), otherwise a
local SQLite file -- same interface, so tests and local demos need no database server."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .schemas import TriageResult

_SCHEMA = """CREATE TABLE IF NOT EXISTS reviews (
    ticket_id TEXT PRIMARY KEY, subject TEXT, body TEXT, result TEXT,
    status TEXT DEFAULT 'pending', reviewer TEXT, decided_at TEXT)"""

_UPSERT = """INSERT INTO reviews (ticket_id, subject, body, result, status) VALUES (?, ?, ?, ?, 'pending')
    ON CONFLICT (ticket_id) DO UPDATE SET subject = excluded.subject, body = excluded.body,
    result = excluded.result, status = 'pending', reviewer = NULL, decided_at = NULL"""


class ReviewStore:
    def __init__(self, path: Path | None = None, database_url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._pg = bool(database_url)
        self._url = database_url
        if self._pg:
            self.db = self._connect_pg()
        else:
            self.db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._exec(_SCHEMA)

    def _connect_pg(self):
        import psycopg  # only needed when deployed

        return psycopg.connect(self._url, autocommit=True)

    def _exec(self, sql: str, params: tuple = ()):
        if not self._pg:
            with self._lock:
                cur = self.db.execute(sql, params)
                self.db.commit()
                return cur
        import psycopg

        sql = sql.replace("?", "%s")
        with self._lock:
            try:
                return self.db.execute(sql, params)
            except psycopg.OperationalError:
                # Serverless instances sit idle and Neon drops idle connections: reconnect once.
                self.db = self._connect_pg()
                return self.db.execute(sql, params)

    def add(self, ticket_id: str, subject: str, body: str, result: TriageResult) -> None:
        self._exec(_UPSERT, (ticket_id, subject, body, result.model_dump_json()))

    def get(self, ticket_id: str) -> tuple[TriageResult, str] | None:
        row = self._exec("SELECT result, status FROM reviews WHERE ticket_id = ?", (ticket_id,)).fetchone()
        return (TriageResult.model_validate_json(row[0]), row[1]) if row else None

    def decide(self, ticket_id: str, status: str, reviewer: str) -> bool:
        """Record a decision once. Returns False if it was already decided (double click, Slack retry)."""
        cur = self._exec(
            "UPDATE reviews SET status = ?, reviewer = ?, decided_at = ? WHERE ticket_id = ? AND status = 'pending'",
            (status, reviewer, datetime.now(timezone.utc).isoformat(), ticket_id),
        )
        return cur.rowcount == 1

    def export_feedback(self) -> list[dict]:
        """Decided reviews as candidate eval rows. Category, urgency and articles still need a human label."""
        rows = self._exec(
            "SELECT ticket_id, subject, body, result, status, reviewer FROM reviews WHERE status != 'pending'"
        ).fetchall()
        out = []
        for tid, subject, body, result, status, reviewer in rows:
            r = json.loads(result)
            out.append({
                "id": tid, "subject": subject, "body": body, "source": "slack_review",
                "bot_reason": r["reason"], "bot_draft": (r.get("draft") or {}).get("reply"),
                "human_status": status, "reviewer": reviewer,
            })
        return out
