"""HTTP entry point.

    uv run uvicorn support_triage.api:app --reload

POST /tickets            generic JSON ticket (email forwarder, scripts, demo)
POST /webhooks/zendesk   Zendesk trigger webhook, writes the result back to the ticket
POST /slack/actions      Slack button clicks (Approve & send / Reject) on escalations
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from collections import OrderedDict
from functools import lru_cache
from urllib.parse import parse_qs

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .config import Settings
from .escalation import make_slack_notifier, verify_slack_signature
from .graph import Pipeline
from .reviews import ReviewStore
from .schemas import Ticket, TriageResult
from .zendesk import ZendeskClient, ZendeskWebhook

log = logging.getLogger(__name__)
app = FastAPI(title="Support Triage")


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def review_store() -> ReviewStore:
    return ReviewStore(settings().review_db)


@lru_cache
def pipeline() -> Pipeline:
    s = settings()
    return Pipeline(s, on_escalate=make_slack_notifier(s.slack_webhook_url, review_store()))


class Seen:
    """Idempotency: webhook providers retry on timeouts, so the same ticket can arrive
    twice. Remember recent ids so a retry doesn't cost a second run or a second reply.
    In-process only -- with several instances, move this to Redis/Postgres."""

    def __init__(self, maxsize: int = 10_000) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        self.maxsize = maxsize

    def claim(self, key: str) -> bool:
        with self._lock:
            if key in self._ids:
                return False
            self._ids[key] = None
            if len(self._ids) > self.maxsize:
                self._ids.popitem(last=False)
            return True


seen = Seen()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/tickets", response_model=TriageResult)
async def triage(ticket: Ticket) -> TriageResult:
    return await run_in_threadpool(pipeline().run, ticket)


def _process_zendesk(payload: ZendeskWebhook) -> None:
    result = pipeline().run(payload.to_ticket())
    ZendeskClient().post_result(payload.ticket_id, result)


@app.post("/webhooks/zendesk", status_code=202)
def zendesk_webhook(
    payload: ZendeskWebhook,
    background: BackgroundTasks,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    expected = os.environ.get("ZENDESK_WEBHOOK_SECRET")
    if not expected or not hmac.compare_digest(x_webhook_secret or "", expected):
        raise HTTPException(status_code=401, detail="bad webhook secret")
    if not seen.claim(f"zendesk:{payload.ticket_id}"):
        return {"status": "duplicate", "ticket_id": payload.ticket_id}
    # Ack immediately (Zendesk times out after ~10s); triage runs after the response.
    background.add_task(_process_zendesk, payload)
    return {"status": "accepted", "ticket_id": payload.ticket_id}


def _update_slack_message(response_url: str, text: str) -> None:
    try:
        httpx.post(response_url, json={"replace_original": True, "text": text}, timeout=10)
    except httpx.HTTPError as e:
        log.error("slack message update failed: %s", e)


@app.post("/slack/actions")
async def slack_actions(request: Request) -> dict:
    body = await request.body()
    secret = settings().slack_signing_secret
    if not secret or not verify_slack_signature(
        secret,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        body,
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(status_code=401, detail="bad slack signature")

    payload = json.loads(parse_qs(body.decode())["payload"][0])
    action = payload["actions"][0]
    ticket_id, choice = action["value"], action["action_id"]
    reviewer = payload["user"].get("username") or payload["user"]["id"]

    found = review_store().get(ticket_id)
    if found is None:
        return {"ok": False, "error": "unknown ticket"}
    result, _ = found
    if not review_store().decide(ticket_id, "approved" if choice == "approve" else "rejected", reviewer):
        return {"ok": True, "note": "already decided"}

    if choice == "approve" and result.draft:
        sent = ticket_id.startswith("zd-") and ZendeskClient().post_approved_reply(
            ticket_id.removeprefix("zd-"), result.draft.reply, reviewer)
        note = "sent to the customer via Zendesk" if sent else "approved (not sent: no Zendesk ticket/config)"
        text = f":white_check_mark: {ticket_id} approved by @{reviewer}, {note}."
    else:
        text = f":raising_hand: {ticket_id}: @{reviewer} is handling it."
    if payload.get("response_url"):
        await run_in_threadpool(_update_slack_message, payload["response_url"], text)
    return {"ok": True}
