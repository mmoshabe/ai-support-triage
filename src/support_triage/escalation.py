"""Human-in-the-loop: escalated tickets are posted to Slack with the draft (if
any) and Approve / Reject buttons, so an agent can review instead of starting
from scratch.

Buttons need a Slack *app* (not a legacy webhook): create one at api.slack.com/apps,
enable Incoming Webhooks and Interactivity, and point the Interactivity Request URL
at https://<your-host>/slack/actions. See README > Slack setup."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

import httpx

from .reviews import ReviewStore
from .schemas import Ticket, TriageResult

log = logging.getLogger(__name__)


def slack_payload(ticket: Ticket, result: TriageResult) -> dict:
    cls = result.classification
    header = f"Escalated ticket {ticket.id}: {ticket.subject}"
    fields = [f"*Why:* {result.reason}"]
    if cls:
        fields.append(f"*Category:* {cls.category.value}  *Urgency:* {cls.urgency.value}  *Confidence:* {cls.confidence:.2f}")
        fields.append(f"*Summary:* {cls.summary}")
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(fields)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Customer wrote:*\n>{ticket.body[:1500]}"}},
    ]
    buttons = []
    if result.draft:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Suggested reply:*\n>{result.draft.reply[:2500]}"}})
        buttons.append({"type": "button", "style": "primary", "action_id": "approve", "value": ticket.id,
                        "text": {"type": "plain_text", "text": "Approve & send draft"}})
    buttons.append({"type": "button", "action_id": "reject", "value": ticket.id,
                    "text": {"type": "plain_text", "text": "I'll handle it"}})
    blocks.append({"type": "actions", "elements": buttons})
    return {"text": header, "blocks": blocks}


def make_slack_notifier(webhook_url: str | None, store: ReviewStore | None = None):
    def notify(ticket: Ticket, result: TriageResult) -> None:
        if store:
            store.add(ticket.id, ticket.subject, ticket.body, result)
        if not webhook_url:
            log.info("escalation (no SLACK_WEBHOOK_URL set): %s - %s", ticket.id, result.reason)
            return
        try:
            httpx.post(webhook_url, json=slack_payload(ticket, result), timeout=10).raise_for_status()
        except httpx.HTTPError as e:
            # The decision is already in traces.jsonl and the review store; a Slack outage must not lose it.
            log.error("slack notify failed for %s: %s", ticket.id, e)

    return notify


def verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str,
                           now: float | None = None) -> bool:
    """https://api.slack.com/authentication/verifying-requests-from-slack"""
    try:
        if abs((now or time.time()) - int(timestamp)) > 300:  # replay protection
            return False
    except ValueError:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
