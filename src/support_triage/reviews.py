"""Escalated tickets awaiting a human decision, plus the decisions themselves.

Every Approve / Reject is stored with the bot's result, which makes it new labelled data:
`export_feedback()` turns it into eval rows, so the test set grows from real traffic.
SQLite keeps it dependency-free; swap for Postgres when you deploy more than one instance."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .schemas import TriageResult


class ReviewStore:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS reviews (
                 ticket_id TEXT PRIMARY KEY, subject TEXT, body TEXT, result TEXT,
                 status TEXT DEFAULT 'pending', reviewer TEXT, decided_at TEXT)"""
        )
        self.db.commit()

    def add(self, ticket_id: str, subject: str, body: str, result: TriageResult) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO reviews (ticket_id, subject, body, result) VALUES (?, ?, ?, ?)",
                (ticket_id, subject, body, result.model_dump_json()),
            )
            self.db.commit()

    def get(self, ticket_id: str) -> tuple[TriageResult, str] | None:
        row = self.db.execute("SELECT result, status FROM reviews WHERE ticket_id = ?", (ticket_id,)).fetchone()
        return (TriageResult.model_validate_json(row[0]), row[1]) if row else None

    def decide(self, ticket_id: str, status: str, reviewer: str) -> bool:
        """Record a decision once. Returns False if it was already decided (double click, Slack retry)."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE reviews SET status = ?, reviewer = ?, decided_at = ? WHERE ticket_id = ? AND status = 'pending'",
                (status, reviewer, datetime.now(timezone.utc).isoformat(), ticket_id),
            )
            self.db.commit()
            return cur.rowcount == 1

    def export_feedback(self) -> list[dict]:
        """Decided reviews as candidate eval rows. Category, urgency and articles still need a human label."""
        rows = self.db.execute(
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
