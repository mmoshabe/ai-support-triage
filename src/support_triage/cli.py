"""Triage one ticket from the command line:

    uv run triage "Can't log in" "I reset my password but the link expired"
"""

from __future__ import annotations

import sys

from .graph import Pipeline
from .schemas import Ticket


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit('usage: triage "<subject>" "<body>"')
    result = Pipeline().run(Ticket(id="cli", subject=sys.argv[1], body=sys.argv[2]))
    print(result.model_dump_json(indent=2))
