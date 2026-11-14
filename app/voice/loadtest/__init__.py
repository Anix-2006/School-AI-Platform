"""HTTP load tester for POST /voice/chat (issue #55)."""

from app.voice.loadtest.runner import SUPPORTED_CONCURRENCY, run_loadtest

__all__ = ["SUPPORTED_CONCURRENCY", "run_loadtest"]
