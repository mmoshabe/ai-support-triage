from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from support_triage import api
from support_triage.escalation import make_slack_notifier, slack_payload, verify_slack_signature
from support_triage.reviews import ReviewStore
from support_triage.schemas import Decision, Draft, Ticket, TriageResult

SECRET = "slack-secret"
TICKET = Ticket(id="zd-42", subject="Refund please", body="Charged twice")
RESULT = TriageResult(ticket_id="zd-42", decision=Decision.escalate, reason="critic flagged",
                      draft=Draft(reply="Hi, sorry about that.", cited_article_ids=[], answerable_from_kb=True))


def signed(body: bytes, ts: int | None = None) -> dict:
    ts = str(ts or int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded"}


def click(action: str, ticket_id: str = "zd-42") -> bytes:
    payload = {"actions": [{"action_id": action, "value": ticket_id}], "user": {"id": "U1", "username": "thandi"}}
    return urlencode({"payload": json.dumps(payload)}).encode()


@pytest.fixture
def client(monkeypatch, tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    store.add(TICKET.id, TICKET.subject, TICKET.body, RESULT)
    sent = []
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setattr(api, "settings", lambda: api.Settings())
    monkeypatch.setattr(api, "review_store", lambda: store)
    monkeypatch.setattr(api.ZendeskClient, "post_approved_reply",
                        lambda self, tid, reply, reviewer: sent.append((tid, reply, reviewer)) or True)
    c = TestClient(api.app)
    c.store, c.sent = store, sent
    return c


def test_signature_verification():
    body, ts = b"payload=x", str(int(time.time()))
    good = signed(body, int(ts))["X-Slack-Signature"]
    assert verify_slack_signature(SECRET, ts, body, good)
    assert not verify_slack_signature(SECRET, ts, b"payload=tampered", good)
    assert not verify_slack_signature("wrong", ts, body, good)
    old = str(int(time.time()) - 600)
    assert not verify_slack_signature(SECRET, old, body, signed(body, int(old))["X-Slack-Signature"])


def test_rejects_unsigned_request(client):
    assert client.post("/slack/actions", content=click("approve")).status_code == 401


def test_approve_sends_draft_once(client):
    body = click("approve")
    assert client.post("/slack/actions", content=body, headers=signed(body)).json() == {"ok": True}
    assert client.sent == [("42", "Hi, sorry about that.", "thandi")]
    # double click / Slack retry: recorded once, nothing re-sent
    assert client.post("/slack/actions", content=body, headers=signed(body)).json()["note"] == "already decided"
    assert len(client.sent) == 1
    assert client.store.export_feedback()[0]["human_status"] == "approved"


def test_reject_records_feedback_without_sending(client):
    body = click("reject")
    client.post("/slack/actions", content=body, headers=signed(body))
    assert client.sent == []
    fb = client.store.export_feedback()[0]
    assert fb["human_status"] == "rejected" and fb["reviewer"] == "thandi"


def test_payload_has_buttons_only_when_useful():
    ids = lambda p: [e["action_id"] for b in p["blocks"] if b["type"] == "actions" for e in b["elements"]]  # noqa: E731
    assert ids(slack_payload(TICKET, RESULT)) == ["approve", "reject"]
    no_draft = RESULT.model_copy(update={"draft": None})
    assert ids(slack_payload(TICKET, no_draft)) == ["reject"]


def test_notifier_stores_review_even_without_slack(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    make_slack_notifier(None, store)(TICKET, RESULT)
    assert store.get("zd-42")[1] == "pending"
