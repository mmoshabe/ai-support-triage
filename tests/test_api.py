from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from support_triage import api
from support_triage.schemas import Decision, Draft, TriageResult
from support_triage.zendesk import comment_for

BODY = {"ticket_id": "123", "subject": "Reset link expired", "description": "help"}


class FakePipeline:
    def __init__(self):
        self.runs = []

    def run(self, ticket):
        self.runs.append(ticket.id)
        return TriageResult(ticket_id=ticket.id, decision=Decision.escalate, reason="test")


@pytest.fixture
def client(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)
    monkeypatch.setattr(api, "pipeline", lambda: fake)
    monkeypatch.setattr(api, "seen", api.Seen())
    c = TestClient(api.app)
    c.fake = fake
    return c


def test_rejects_bad_secret(client):
    assert client.post("/webhooks/zendesk", json=BODY, headers={"X-Webhook-Secret": "nope"}).status_code == 401
    assert client.post("/webhooks/zendesk", json=BODY).status_code == 401


def test_duplicate_delivery_processed_once(client):
    h = {"X-Webhook-Secret": "s3cret"}
    assert client.post("/webhooks/zendesk", json=BODY, headers=h).json()["status"] == "accepted"
    assert client.post("/webhooks/zendesk", json=BODY, headers=h).json()["status"] == "duplicate"
    assert client.fake.runs == ["zd-123"]


def test_auto_reply_is_internal_note_unless_auto_send():
    r = TriageResult(ticket_id="x", decision=Decision.auto_reply, reason="ok",
                     draft=Draft(reply="Hi there", cited_article_ids=[], answerable_from_kb=True))
    assert comment_for(r, auto_send=False)["public"] is False
    assert comment_for(r, auto_send=True) == {"body": "Hi there", "public": True}
    esc = r.model_copy(update={"decision": Decision.escalate})
    assert comment_for(esc, auto_send=True)["public"] is False


def test_tickets_requires_token(client, monkeypatch):
    body = {"id": "t", "subject": "s", "body": "b"}
    monkeypatch.delenv("TRIAGE_API_TOKEN", raising=False)
    assert client.post("/tickets", json=body).status_code == 503  # fails closed when unconfigured
    monkeypatch.setenv("TRIAGE_API_TOKEN", "tok")
    assert client.post("/tickets", json=body).status_code == 401
    assert client.post("/tickets", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.fake.runs == []  # nothing reached the pipeline
    ok = client.post("/tickets", json=body, headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200 and client.fake.runs == ["t"]
