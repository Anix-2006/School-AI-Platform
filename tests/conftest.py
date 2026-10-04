import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest


@pytest.fixture(autouse=True)
def no_intent_classifier(monkeypatch):
    """Keep tests off the embeddings API; tests that need a classifier set
    orchestrator.intent_classifier to a fake themselves."""
    from app.agents import orchestrator

    monkeypatch.setattr(orchestrator, "intent_classifier", None)
