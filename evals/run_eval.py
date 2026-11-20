"""Run the pipeline over the labelled dataset and report the numbers that go in the README.

    uv run python evals/run_eval.py                 # full set
    uv run python evals/run_eval.py --limit 5       # smoke test (~5 tickets of API spend)
    uv run python evals/run_eval.py --retrieval-only  # free: no LLM calls
    uv run python evals/run_eval.py --split dev --judge  # tune on dev, grade replies with an LLM judge
    uv run python evals/run_eval.py --split test         # held-out number for the README

Metrics
- category / urgency accuracy (classifier)
- decision accuracy, plus the two errors that matter differently:
    * unsafe auto-replies: auto_reply where a human was required  (minimise -> safety)
    * unnecessary escalations: escalate where auto_reply was fine (minimise -> efficiency)
- retrieval recall@k against relevant_articles (no LLM needed)
- cost and latency per ticket (mean, p95)
- (--judge) reply quality: share of auto-replies passing grounded / resolves / safe / tone
Each run is saved to evals/results/<timestamp>.json so prompt changes can be compared before/after.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from support_triage.config import Settings
from support_triage.graph import Pipeline
from support_triage.kb import BM25Retriever, load_articles
from support_triage.llm import LLM, LLMError
from support_triage.schemas import Ticket

sys.path.insert(0, str(Path(__file__).parent))
from judge import judge  # noqa: E402

HERE = Path(__file__).parent


def load(split: str, limit: int | None) -> list[dict]:
    rows = [json.loads(line) for line in (HERE / "dataset.jsonl").read_text().splitlines() if line.strip()]
    if split != "all":
        rows = [r for r in rows if r["split"] == split]
    return rows[:limit] if limit else rows


def retrieval_recall(rows: list[dict], settings: Settings) -> float:
    r = BM25Retriever(load_articles(settings.kb_dir))
    hits = total = 0
    for row in rows:
        if not row["relevant_articles"]:
            continue
        got = {a.id for a in r.search(f"{row['subject']} {row['body']}", k=settings.top_k_articles)}
        hits += len(got & set(row["relevant_articles"]))
        total += len(row["relevant_articles"])
    return hits / total if total else 0.0


def p95(xs: list[float]) -> float:
    return sorted(xs)[max(0, int(round(0.95 * len(xs))) - 1)] if xs else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--judge", action="store_true", help="grade auto-replies with an LLM judge")
    ap.add_argument("--judge-model", default="claude-opus-5-5")
    ap.add_argument("--tag", default="", help="label for this run, e.g. 'v1 stricter critic'")
    args = ap.parse_args()

    settings = Settings()
    rows = load(args.split, args.limit)
    recall = retrieval_recall(rows, settings)
    print(f"retrieval recall@{settings.top_k_articles}: {recall:.0%}")
    if args.retrieval_only:
        return

    pipe = Pipeline(settings)
    tickets = [Ticket(id=r["id"], subject=r["subject"], body=r["body"]) for r in rows]
    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(pipe.run, tickets))

    per_ticket, cat_ok, urg_ok, dec_ok, unsafe, unneeded = [], 0, 0, 0, [], []
    for row, res in zip(rows, results):
        cls = res.classification
        c = bool(cls and cls.category.value == row["category"])
        u = bool(cls and cls.urgency.value == row["urgency"])
        d = res.decision.value == row["expected_decision"]
        cat_ok += c; urg_ok += u; dec_ok += d
        if res.decision.value == "auto_reply" and row["expected_decision"] == "escalate":
            unsafe.append(row["id"])
        if res.decision.value == "escalate" and row["expected_decision"] == "auto_reply":
            unneeded.append(row["id"])
        per_ticket.append({
            "id": row["id"], "expected": row, "decision": res.decision.value, "reason": res.reason,
            "category": cls.category.value if cls else None, "urgency": cls.urgency.value if cls else None,
            "cost_usd": res.total_cost_usd, "latency_ms": res.total_latency_ms,
            "reply": res.draft.reply if res.draft else None,
        })

    judge_summary = None
    if args.judge:
        llm, by_id = LLM(), {a.id: a for a in pipe.retriever.articles}
        grades, judge_cost = [], 0.0
        for row, res, t in zip(rows, results, per_ticket):
            if res.decision.value != "auto_reply" or not res.draft:
                continue
            arts = [by_id[i] for s in res.steps if s.name == "retrieve" for i in s.output["article_ids"]]
            try:
                jr = judge(llm, args.judge_model, f"{row['subject']}\n\n{row['body']}", arts, res.draft.reply)
                g, judge_cost = jr.parsed, judge_cost + jr.cost_usd
            except LLMError as e:
                t["judge_error"] = str(e)
                continue
            t["judge"] = g.model_dump()
            grades.append(g)
        crit = ["grounded", "resolves", "safe", "tone"]
        judge_summary = {c: sum(getattr(g, c) for g in grades) / len(grades) for c in crit} if grades else {}
        judge_summary["all_pass"] = sum(all(getattr(g, c) for c in crit) for g in grades) / len(grades) if grades else None
        judge_summary["graded"] = len(grades)
        judge_summary["judge_cost_usd"] = round(judge_cost, 4)

    # Retrieval recall of what the pipeline actually retrieved (only tickets that reached retrieval).
    hit = tot = 0
    for row, res in zip(rows, results):
        got = {i for st in res.steps if st.name == "retrieve" for i in st.output["article_ids"]}
        if got and row["relevant_articles"]:
            hit += len(got & set(row["relevant_articles"])); tot += len(row["relevant_articles"])
    pipeline_recall = hit / tot if tot else None

    n = len(rows)
    costs = [r.total_cost_usd for r in results]
    lats = [r.total_latency_ms / 1000 for r in results]
    summary = {
        "tag": args.tag,
        "split": args.split,
        "n": n,
        "models": {"classifier": settings.classifier_model, "drafter": settings.drafter_model, "critic": settings.critic_model},
        "category_accuracy": cat_ok / n,
        "urgency_accuracy": urg_ok / n,
        "decision_accuracy": dec_ok / n,
        "unsafe_auto_replies": unsafe,
        "unnecessary_escalations": unneeded,
        "auto_reply_rate": sum(r.decision.value == "auto_reply" for r in results) / n,
        "retrieval_recall": recall,
        "pipeline_retrieval_recall": pipeline_recall,
        "cost_per_ticket_usd": {"mean": statistics.mean(costs), "p95": p95(costs), "total": sum(costs)},
        "latency_s": {"mean": statistics.mean(lats), "p95": p95(lats)},
        "reply_quality": judge_summary,
    }

    out = HERE / "results" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "tickets": per_ticket}, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"\nsaved {out.relative_to(HERE.parent)}")
    sys.exit(1 if unsafe else 0)  # an unsafe auto-reply fails CI


if __name__ == "__main__":
    main()
