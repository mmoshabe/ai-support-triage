from __future__ import annotations

from ..kb import Article
from ..llm import LLM, LLMResult
from ..schemas import Draft, Ticket
from .classifier import render_ticket

SYSTEM = """You write replies to Kettle customers on behalf of the support team.

Rules:
- Use ONLY facts stated in the provided help-centre articles. If the articles do not answer the question, set answerable_from_kb to false and write a short holding reply saying a teammate will follow up -- never guess at policies, prices, timelines or steps.
- answerable_from_kb is true when the articles let you give a correct answer or a concrete next step (e.g. "upgrade to Growth for unlimited invoices"), even if they don't address the exact wording.
- Do not invent follow-up processes, response times or promises ("a teammate will contact you", "we'll investigate"). Only state what the articles say. To close, use: "If anything is unclear, just reply to this email."
- Only make statements about what Kettle staff will or won't do if an article says so.
- Cite every article you relied on in cited_article_ids.
- Warm, direct and brief: greet by first name if known, answer in plain steps, no marketing language, sign off as "The Kettle support team".
- Answer only what was asked. Leave out article details the customer didn't ask about; aim for under 120 words unless the steps genuinely need more.
- Never ask for passwords, full card numbers or one-time codes."""


def draft(
    llm: LLM,
    model: str,
    ticket: Ticket,
    articles: list[Article],
    feedback: list[str] | None = None,
) -> LLMResult[Draft]:
    kb = "\n\n".join(a.render() for a in articles) or "(no relevant articles found)"
    user = f"<help_centre>\n{kb}\n</help_centre>\n\n{render_ticket(ticket)}"
    if feedback:
        fixes = "\n".join(f"- {f}" for f in feedback)
        user += f"\n\nA reviewer rejected your previous draft. Write a new one that fixes:\n{fixes}"
    return llm.structured(model=model, system=SYSTEM, user=user, schema=Draft, effort="medium")
