"""Zendesk integration.

Inbound: a Zendesk trigger + webhook POSTs JSON to /webhooks/zendesk. Configure the
webhook body in Zendesk as:
    {"ticket_id": "{{ticket.id}}", "subject": "{{ticket.title}}",
     "description": "{{ticket.description}}", "requester_email": "{{ticket.requester.email}}"}
and add header X-Webhook-Secret: <ZENDESK_WEBHOOK_SECRET>.

Outbound: the result is written back as a comment. Everything is an INTERNAL note
unless ZENDESK_AUTO_SEND=true, so you can shadow-run against live traffic and compare
the bot's decisions with what your agents actually did before letting it reply.
"""

from __future__ import annotations

import logging
import os
import re

import httpx
from pydantic import BaseModel

from .schemas import Decision, Ticket, TriageResult

log = logging.getLogger(__name__)


class ZendeskWebhook(BaseModel):
    ticket_id: str
    subject: str
    description: str
    requester_email: str | None = None

    def to_ticket(self) -> Ticket:
        return Ticket(id=f"zd-{self.ticket_id}", subject=self.subject, body=self.description,
                      customer_email=self.requester_email)


def comment_for(result: TriageResult, auto_send: bool) -> dict:
    if result.decision is Decision.auto_reply and result.draft and auto_send:
        return {"body": result.draft.reply, "public": True}
    cls = result.classification
    lines = [f"[triage bot] decision={result.decision.value}: {result.reason}"]
    if cls:
        lines.append(f"category={cls.category.value} urgency={cls.urgency.value} confidence={cls.confidence:.2f}")
    if result.draft:
        lines += ["", "Suggested reply:", result.draft.reply]
    return {"body": "\n".join(lines), "public": False}


class ZendeskClient:
    def __init__(self) -> None:
        self.subdomain = os.environ.get("ZENDESK_SUBDOMAIN")
        self.email = os.environ.get("ZENDESK_EMAIL")
        self.token = os.environ.get("ZENDESK_API_TOKEN")
        self.auto_send = os.environ.get("ZENDESK_AUTO_SEND", "").lower() == "true"

    @property
    def configured(self) -> bool:
        return bool(self.subdomain and self.email and self.token)

    def post_result(self, zendesk_ticket_id: str, result: TriageResult) -> None:
        tags = ["triage_bot", f"triage_{result.decision.value}"]
        if result.classification:
            tags.append(f"triage_{result.classification.category.value}")
        self._comment(zendesk_ticket_id, comment_for(result, self.auto_send), tags)

    def post_approved_reply(self, zendesk_ticket_id: str, reply: str, reviewer: str) -> bool:
        """A human approved the bot's draft in Slack: send it to the customer."""
        return self._comment(zendesk_ticket_id, {"body": reply, "public": True},
                             ["triage_bot", "triage_human_approved", f"approved_by_{re.sub(r'[^a-z0-9_]', '_', reviewer.lower())}"])

    def _comment(self, zendesk_ticket_id: str, comment: dict, tags: list[str]) -> bool:
        if not self.configured:
            log.info("zendesk not configured; would post to %s: %s", zendesk_ticket_id, comment)
            return False
        try:
            httpx.put(
                f"https://{self.subdomain}.zendesk.com/api/v2/tickets/{zendesk_ticket_id}.json",
                json={"ticket": {"comment": comment, "additional_tags": tags}},
                auth=(f"{self.email}/token", self.token),
                timeout=15,
            ).raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.error("zendesk write-back failed for %s: %s", zendesk_ticket_id, e)
            return False
