"""Runtime configuration. Everything is overridable via environment variables so
eval runs can compare models / thresholds without code changes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load secrets (ANTHROPIC_API_KEY, SLACK_WEBHOOK_URL, ...) from a gitignored .env.
# Real environment variables win over the file.
load_dotenv(PROJECT_ROOT / ".env", override=False)

# USD per 1M tokens: (input, output, cache_read). Used for cost-per-ticket reporting.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    classifier_model: str = field(default_factory=lambda: _env("TRIAGE_CLASSIFIER_MODEL", "claude-opus-5-5"))
    drafter_model: str = field(default_factory=lambda: _env("TRIAGE_DRAFTER_MODEL", "claude-opus-5-5"))
    critic_model: str = field(default_factory=lambda: _env("TRIAGE_CRITIC_MODEL", "claude-opus-5-5"))

    # Below this classifier confidence the ticket goes straight to a human.
    min_classifier_confidence: float = field(
        default_factory=lambda: float(_env("TRIAGE_MIN_CLASSIFIER_CONFIDENCE", "0.6"))
    )
    # Critic score (0-1) a draft must reach to be auto-approved.
    min_critic_score: float = field(default_factory=lambda: float(_env("TRIAGE_MIN_CRITIC_SCORE", "0.75")))
    max_revisions: int = field(default_factory=lambda: int(_env("TRIAGE_MAX_REVISIONS", "1")))
    top_k_articles: int = field(default_factory=lambda: int(_env("TRIAGE_TOP_K", "3")))

    kb_dir: Path = field(default_factory=lambda: Path(_env("TRIAGE_KB_DIR", str(PROJECT_ROOT / "kb"))))
    trace_log: Path = field(
        default_factory=lambda: Path(_env("TRIAGE_TRACE_LOG", str(PROJECT_ROOT / "traces.jsonl")))
    )
    slack_webhook_url: str | None = field(default_factory=lambda: os.environ.get("SLACK_WEBHOOK_URL"))
    slack_signing_secret: str | None = field(default_factory=lambda: os.environ.get("SLACK_SIGNING_SECRET"))
    review_db: Path = field(
        default_factory=lambda: Path(_env("TRIAGE_REVIEW_DB", str(PROJECT_ROOT / "reviews.sqlite3")))
    )
