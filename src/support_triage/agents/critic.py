from __future__ import annotations

from ..kb import Article
from ..llm import LLM, LLMResult
from ..schemas import Critique, Draft, Ticket
from .classifier import render_ticket

SYSTEM = """You are the quality gate before a support reply is sent to a Kettle customer without human review.

Check the draft against the help-centre articles and the ticket:
1. Grounding: list every factual statement (prices, timelines, steps, policies) that the articles do not support in unsupported_claims.
2. Correctness and completeness: does it actually resolve what the customer asked?
3. Safety: does it ask for secrets, promise refunds/credits, or commit Kettle to anything not in the articles?
4. Tone: warm, brief, plain.

Score 0-1. A draft with any unsupported claim must score below 0.75.
Set must_escalate to true when a human must own the case anyway, however good the draft is:
- resolving it needs a Kettle staff member to act: issue a refund, verify identity or ownership, change billing, investigate a bug or incorrect data, or make a policy exception
- the customer threatens legal action or a chargeback
- the reply declines a request for money back from Kettle (even when the policy is clear, a person sends refusals)
- it is a suspected security problem
Do NOT escalate just because the customer is frustrated, or because they will need to do something themselves in the product.
issues must be concrete edits, not general advice."""


def critique(
    llm: LLM,
    model: str,
    ticket: Ticket,
    articles: list[Article],
    candidate: Draft,
) -> LLMResult[Critique]:
    kb = "\n\n".join(a.render() for a in articles) or "(no relevant articles found)"
    user = (
        f"<help_centre>\n{kb}\n</help_centre>\n\n{render_ticket(ticket)}\n\n"
        f"<draft cited=\"{', '.join(candidate.cited_article_ids)}\">\n{candidate.reply}\n</draft>"
    )
    return llm.structured(model=model, system=SYSTEM, user=user, schema=Critique, effort="medium")
