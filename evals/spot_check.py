"""Check the LLM judge against your own judgement.

    uv run python evals/spot_check.py sample   # writes evals/spot_check.md with 20 graded replies
    # open the file and, for each one, mark your verdict: [x] agree  or  [x] disagree (+ a note)
    uv run python evals/spot_check.py score    # prints the agreement rate for the README

The sample is a fixed random draw (seeded) from the latest run that has judge grades,
with every judge FAIL included first, because disagreements are most likely there.
"""

from __future__ import annotations

import glob
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "spot_check.md"
CRIT = ("grounded", "resolves", "safe", "tone")


def latest_judged_run() -> Path:
    for f in sorted(glob.glob(str(HERE / "results" / "*.json")), reverse=True):
        if any("judge" in t for t in json.load(open(f))["tickets"]):
            return Path(f)
    sys.exit("no results with judge grades yet: run run_eval.py --judge first")


def sample(n: int = 20) -> None:
    run = latest_judged_run()
    graded = [t for t in json.load(open(run))["tickets"] if "judge" in t]
    fails = [t for t in graded if not all(t["judge"][c] for c in CRIT)]
    passes = [t for t in graded if t not in fails]
    random.Random(0).shuffle(passes)
    picked = (fails + passes)[:n]

    lines = [f"# Judge spot-check\n\nSource run: `{run.name}`. For each reply, tick ONE box.\n"]
    for t in picked:
        e, j = t["expected"], t["judge"]
        verdict = "PASS" if all(j[c] for c in CRIT) else "FAIL (" + ", ".join(c for c in CRIT if not j[c]) + ")"
        lines += [
            f"## {t['id']}: {e['subject']}",
            f"**Ticket:** {e['body']}\n",
            "**Reply sent:**\n",
            "\n".join("> " + l for l in t["reply"].splitlines()),
            f"\n**Judge:** {verdict}. {j['explanation']}\n",
            "- [ ] agree\n- [ ] disagree. Note:\n",
        ]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT.relative_to(HERE.parent)} with {len(picked)} replies ({len(fails)} judge fails)")


def score() -> None:
    text = OUT.read_text()
    agree = len(re.findall(r"- \[x\] agree", text, re.I))
    disagree = len(re.findall(r"- \[x\] disagree", text, re.I))
    if agree + disagree == 0:
        sys.exit("no boxes ticked yet")
    print(f"judge agreement: {agree}/{agree + disagree} = {agree / (agree + disagree):.0%}")


if __name__ == "__main__":
    {"sample": sample, "score": score}[sys.argv[1] if len(sys.argv) > 1 else "sample"]()
