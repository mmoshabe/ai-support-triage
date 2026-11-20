"""The triage graph. Control flow lives in code, not in a prompt, so every route
is deterministic, testable and visible in the trace:

    classify --(security / low confidence / error)--> escalate
        |
    retrieve -> draft --(not answerable)--> escalate
                  |
               critique --(must_escalate)--> escalate
                  |  ^
     (score low)  +--+ revise (<= max_revisions), then escalate
                  |
              auto_reply
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Callable

from . import agents
from .config import Settings
from .kb import Article, BM25Retriever, Retriever, load_articles
from .llm import LLM, LLMError, LLMResult
from .schemas import Category, Decision, Step, Ticket, TriageResult

ALWAYS_HUMAN = {Category.security}


def fused_search(retriever: Retriever, queries: list[str], k: int, rrf_k: int = 60) -> list[Article]:
    """Reciprocal-rank fusion over several queries: the classifier's rewritten query
    catches intent ("plan limit"), the raw ticket catches exact terms the rewrite dropped."""
    scores: dict[str, float] = {}
    by_id: dict[str, Article] = {}
    for q in queries:
        for rank, a in enumerate(retriever.search(q, k=k)):
            scores[a.id] = scores.get(a.id, 0.0) + 1 / (rrf_k + rank + 1)
            by_id[a.id] = a
    return [by_id[i] for i in sorted(scores, key=scores.get, reverse=True)[:k]]


class Pipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLM | None = None,
        retriever: Retriever | None = None,
        on_escalate: Callable[[Ticket, TriageResult], None] | None = None,
    ) -> None:
        self.s = settings or Settings()
        self.llm = llm or LLM()
        self.retriever = retriever or BM25Retriever(load_articles(self.s.kb_dir))
        self.on_escalate = on_escalate

    # -- helpers ----------------------------------------------------------

    def _call(self, result: TriageResult, name: str, fn: Callable[[], LLMResult]) -> LLMResult | None:
        """Run one agent, record a trace step, and swallow failures into the trace."""
        try:
            r = fn()
        except LLMError as e:
            result.steps.append(Step(name=name, latency_ms=0, error=str(e)))
            return None
        result.steps.append(
            Step(name=name, model=r.model, latency_ms=r.latency_ms, cost_usd=r.cost_usd,
                 output=r.parsed.model_dump(mode="json"))
        )
        return r

    def _finish(self, ticket: Ticket, result: TriageResult, decision: Decision, reason: str) -> TriageResult:
        result.decision, result.reason = decision, reason
        result.total_cost_usd = round(sum(s.cost_usd for s in result.steps), 6)
        result.total_latency_ms = sum(s.latency_ms for s in result.steps)
        self._log(result)
        if decision is Decision.escalate and self.on_escalate:
            self.on_escalate(ticket, result)
        return result

    def _log(self, result: TriageResult) -> None:
        try:
            with self.s.trace_log.open("a") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **result.model_dump(mode="json")}) + "\n")
        except OSError:
            pass  # tracing must never take the pipeline down

    # -- the graph --------------------------------------------------------

    def run(self, ticket: Ticket) -> TriageResult:
        res = TriageResult(ticket_id=ticket.id, decision=Decision.escalate, reason="")
        esc = lambda why: self._finish(ticket, res, Decision.escalate, why)  # noqa: E731

        c = self._call(res, "classify", lambda: agents.classify(self.llm, self.s.classifier_model, ticket))
        if c is None:
            return esc("classifier failed")
        cls = res.classification = c.parsed
        if cls.category in ALWAYS_HUMAN:
            return esc(f"category '{cls.category.value}' always goes to a human")
        if cls.manipulation_attempt:
            return esc("possible manipulation attempt: needs human review")
        if cls.confidence < self.s.min_classifier_confidence:
            return esc(f"low classifier confidence ({cls.confidence:.2f})")

        t0 = time.perf_counter()
        articles = fused_search(
            self.retriever, [cls.search_query, f"{ticket.subject} {ticket.body}"], self.s.top_k_articles
        )
        res.steps.append(Step(name="retrieve", latency_ms=int((time.perf_counter() - t0) * 1000),
                              output={"article_ids": [a.id for a in articles]}))
        if not articles:
            return esc("no knowledge-base articles matched")

        feedback: list[str] | None = None
        for attempt in range(self.s.max_revisions + 1):
            d = self._call(res, f"draft#{attempt}",
                           lambda: agents.draft(self.llm, self.s.drafter_model, ticket, articles, feedback))
            if d is None:
                return esc("drafter failed")
            res.draft, res.revisions = d.parsed, attempt
            if not d.parsed.answerable_from_kb:
                return esc("knowledge base does not answer this ticket")

            k = self._call(res, f"critique#{attempt}",
                           lambda: agents.critique(self.llm, self.s.critic_model, ticket, articles, d.parsed))
            if k is None:
                return esc("critic failed")
            crit = res.critique = k.parsed
            if crit.must_escalate:
                return esc("critic flagged for human ownership")
            if crit.score >= self.s.min_critic_score and not crit.unsupported_claims:
                return self._finish(ticket, res, Decision.auto_reply, f"critic score {crit.score:.2f}")
            feedback = crit.issues + [f"Remove unsupported claim: {u}" for u in crit.unsupported_claims]

        return esc(f"draft still below quality bar after {self.s.max_revisions} revision(s)")
