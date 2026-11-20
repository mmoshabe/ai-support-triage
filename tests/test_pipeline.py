"""Routing tests with a scripted fake LLM -- no API calls, runs in CI for free."""

from __future__ import annotations

from dataclasses import replace

import pytest

from support_triage.config import PROJECT_ROOT, Settings
from support_triage.graph import Pipeline
from support_triage.kb import BM25Retriever, load_articles
from support_triage.llm import LLMError, LLMResult
from support_triage.schemas import Classification, Critique, Decision, Draft, Ticket


def cls(category="account_access", confidence=0.9, manipulation=False):
    return Classification(category=category, urgency="normal", confidence=confidence,
                          manipulation_attempt=manipulation,
                          search_query="password reset link expired", summary="s")


def drft(answerable=True):
    return Draft(reply="Hi! Request a new link.", cited_article_ids=["password-reset"], answerable_from_kb=answerable)


def crit(score=0.9, unsupported=(), must_escalate=False):
    return Critique(score=score, unsupported_claims=list(unsupported), issues=["fix x"], must_escalate=must_escalate)


class FakeLLM:
    """Returns queued outputs per schema; an Exception in the queue is raised."""

    def __init__(self, **queues):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls: list[str] = []

    def structured(self, *, model, system, user, schema, **_):
        name = schema.__name__
        self.calls.append(name)
        item = self.queues[name].pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(parsed=item, model=model, latency_ms=5, cost_usd=0.001, request_id=None)


@pytest.fixture
def settings(tmp_path):
    return replace(Settings(), trace_log=tmp_path / "t.jsonl", kb_dir=PROJECT_ROOT / "kb")


TICKET = Ticket(id="x", subject="Password reset link expired", body="link expired")


def run(settings, llm, **kw):
    escalated = []
    p = Pipeline(settings, llm=llm, on_escalate=lambda t, r: escalated.append(r), **kw)
    return p.run(TICKET), escalated


def test_happy_path_auto_replies(settings):
    llm = FakeLLM(Classification=[cls()], Draft=[drft()], Critique=[crit()])
    res, escalated = run(settings, llm)
    assert res.decision is Decision.auto_reply
    assert not escalated
    assert res.total_cost_usd == pytest.approx(0.003)
    assert settings.trace_log.read_text().count("\n") == 1


def test_security_always_escalates_without_drafting(settings):
    llm = FakeLLM(Classification=[cls(category="security")])
    res, escalated = run(settings, llm)
    assert res.decision is Decision.escalate and escalated
    assert llm.calls == ["Classification"]


def test_low_confidence_escalates(settings):
    res, _ = run(settings, FakeLLM(Classification=[cls(confidence=0.3)]))
    assert res.decision is Decision.escalate and "confidence" in res.reason


def test_manipulation_attempt_escalates_before_drafting(settings):
    llm = FakeLLM(Classification=[cls(manipulation=True)])
    res, _ = run(settings, llm)
    assert res.decision is Decision.escalate and "manipulation" in res.reason
    assert llm.calls == ["Classification"]


def test_fused_search_combines_queries():
    from support_triage.graph import fused_search
    r = BM25Retriever(load_articles(PROJECT_ROOT / "kb"))
    ids = [a.id for a in fused_search(r, ["monthly invoice limit upgrade plan", "reached 20 invoices"], k=3)]
    assert "plans-and-pricing" in ids


def test_unanswerable_escalates(settings):
    res, _ = run(settings, FakeLLM(Classification=[cls()], Draft=[drft(answerable=False)]))
    assert res.decision is Decision.escalate and "knowledge base" in res.reason


def test_revision_loop_then_approve(settings):
    llm = FakeLLM(Classification=[cls()], Draft=[drft(), drft()],
                  Critique=[crit(score=0.9, unsupported=["made-up fee"]), crit(score=0.9)])
    res, _ = run(settings, llm)
    assert res.decision is Decision.auto_reply and res.revisions == 1


def test_revision_budget_exhausted_escalates(settings):
    llm = FakeLLM(Classification=[cls()], Draft=[drft(), drft()], Critique=[crit(score=0.4), crit(score=0.5)])
    res, escalated = run(settings, llm)
    assert res.decision is Decision.escalate and escalated
    assert res.draft is not None  # human gets the best draft to edit


def test_critic_must_escalate(settings):
    res, _ = run(settings, FakeLLM(Classification=[cls()], Draft=[drft()], Critique=[crit(must_escalate=True)]))
    assert res.decision is Decision.escalate


def test_llm_failure_degrades_to_escalation(settings):
    res, _ = run(settings, FakeLLM(Classification=[LLMError("model refused")]))
    assert res.decision is Decision.escalate
    assert res.steps[0].error == "model refused"


def test_retriever_finds_relevant_article():
    r = BM25Retriever(load_articles(PROJECT_ROOT / "kb"))
    assert r.search("bank feed reconnect required FNB")[0].id == "bank-sync"
    assert r.search("2FA lost phone backup codes")[0].id == "two-factor-authentication"
