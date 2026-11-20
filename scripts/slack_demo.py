"""Post a real escalation from a past eval run to Slack -- no Claude API calls, no cost.

    uv run python scripts/slack_demo.py            # default: t003 (duplicate charge, has a draft)
    uv run python scripts/slack_demo.py t005       # any escalated ticket id from the eval runs

The review is stored in reviews.sqlite3, so the Approve / I'll handle it buttons work
as long as the API server (uvicorn) is running and reachable from Slack.
"""

from __future__ import annotations

import json
import sys
import time

from support_triage.config import PROJECT_ROOT, Settings
from support_triage.escalation import make_slack_notifier
from support_triage.reviews import ReviewStore
from support_triage.schemas import Ticket, TriageResult


def main() -> None:
    want = sys.argv[1] if len(sys.argv) > 1 else "t003"
    rows = {r["id"]: r for r in map(json.loads, (PROJECT_ROOT / "evals/dataset.jsonl").read_text().splitlines())}
    result = None
    for line in reversed((PROJECT_ROOT / "traces.jsonl").read_text().splitlines()):
        r = json.loads(line)
        if r["ticket_id"] == want and r["decision"] == "escalate":
            result = TriageResult.model_validate(r)
            break
    if result is None:
        sys.exit(f"no escalation for {want} in traces.jsonl")

    s = Settings()
    if not s.slack_webhook_url:
        sys.exit("SLACK_WEBHOOK_URL is not set in .env")
    row = rows[want]
    demo_id = f"demo-{want}-{int(time.time())}"  # unique, so you can click the buttons again on each run
    ticket = Ticket(id=demo_id, subject=row["subject"], body=row["body"])
    result.ticket_id = demo_id
    make_slack_notifier(s.slack_webhook_url, ReviewStore(s.review_db))(ticket, result)
    print(f"posted {demo_id} to Slack ({result.reason})")


if __name__ == "__main__":
    main()
