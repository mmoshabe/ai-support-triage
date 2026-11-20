"""Vercel entry point: exposes the FastAPI app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from support_triage.api import app  # noqa: E402,F401
