"""Intent classifier scoring, router fallback rules, shipped model, and the
labeled dataset. Run with: pytest tests/test_intent_classifier.py
No network access needed - embeddings and LLMs are faked."""

import json
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents import orchestrator
from app.agents.intent_classifier import MODEL_PATH, IntentClassifier, IntentPrediction

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "intent"


class FakeClassifier:
    def __init__(self, intent=None, confidence=0.0, error=None):
        self.intent, self.confidence, self.error = intent, confidence, error
        self.texts: list[str] = []

    def predict(self, text):
        self.texts.append(text)
        if self.error:
            raise self.error
        return IntentPrediction(self.intent, self.confidence)


class FakeRouterLLM:
    def __init__(self, reply="communication"):
        self.reply, self.called = reply, False

    def invoke(self, messages):
        self.called = True
        return AIMessage(content=self.reply)


def _route(monkeypatch, classifier, llm_reply="communication", messages=None):
    llm = FakeRouterLLM(llm_reply)
    monkeypatch.setattr(orchestrator, "intent_classifier", classifier)
    monkeypatch.setattr(orchestrator, "router_llm", llm)
    monkeypatch.setattr(orchestrator.settings, "intent_confidence_threshold", 0.6)
    state = {"messages": messages or [HumanMessage(content="How did she do in FA1?")]}
    return orchestrator.router_node(state), llm


def test_confident_classifier_skips_llm(monkeypatch):
    result, llm = _route(monkeypatch, FakeClassifier("academic", 0.93))
    assert result == {
        "route_to": "academic",
        "intent": "academic",
        "intent_source": "classifier",
        "intent_confidence": 0.93,
    }
    assert not llm.called


def test_low_confidence_falls_back_to_llm(monkeypatch):
    result, llm = _route(monkeypatch, FakeClassifier("academic", 0.41), llm_reply="daily_update")
    assert result["route_to"] == "daily_update"
    assert result["intent_source"] == "llm"
    assert result["intent_confidence"] is None
    assert llm.called


def test_classifier_error_falls_back_to_llm(monkeypatch):
    result, _ = _route(monkeypatch, FakeClassifier(error=TimeoutError()), llm_reply="academic")
    assert (result["route_to"], result["intent_source"]) == ("academic", "llm")


def test_missing_classifier_uses_llm(monkeypatch):
    result, _ = _route(monkeypatch, None, llm_reply="parent_teacher")
    assert (result["route_to"], result["intent_source"]) == ("parent_teacher", "llm")


def test_unknown_classifier_label_falls_back_to_llm(monkeypatch):
    result, _ = _route(monkeypatch, FakeClassifier("insight", 0.99))
    assert result["intent_source"] == "llm"


def test_classifier_sees_latest_parent_message(monkeypatch):
    classifier = FakeClassifier("daily_update", 0.9)
    history = [
        HumanMessage(content="How did she do in FA1?"),
        AIMessage(content="She scored 42/50."),
        HumanMessage(content="  Was she present today?  "),
    ]
    _route(monkeypatch, classifier, messages=history)
    assert classifier.texts == ["Was she present today?"]


def test_scoring_is_softmax_over_logits():
    classifier = IntentClassifier(
        labels=["a", "b"],
        coef=np.array([[2.0, 0.0], [0.0, 2.0]]),
        intercept=np.zeros(2),
        embedding_model="fake",
        embed=lambda texts, model: np.array([[1.0, 0.0]]),
    )
    prediction = classifier.predict("anything")
    assert prediction.intent == "a"
    assert prediction.confidence == pytest.approx(1 / (1 + np.exp(-2.0)))


def test_shipped_model_matches_routes():
    data = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    assert set(data["labels"]) == set(orchestrator.ROUTE_TO_NODE)
    coef = np.array(data["coef"])
    assert coef.shape[0] == len(data["labels"])
    assert len(data["intercept"]) == len(data["labels"])
    assert IntentClassifier.load() is not None


def _rows(name):
    path = DATA_DIR / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _normalize(text):
    return unicodedata.normalize("NFC", text).casefold().strip(" ?.!")


@pytest.mark.parametrize("name, minimum", [("train.jsonl", 30), ("test.jsonl", 10)])
def test_dataset_covers_every_intent(name, minimum):
    rows = _rows(name)
    assert {r["intent"] for r in rows} == set(orchestrator.ROUTE_TO_NODE)
    counts = Counter(r["intent"] for r in rows)
    assert min(counts.values()) >= minimum, counts
    assert {"en", "hinglish", "hi", "te"} <= {r["lang"] for r in rows}


def test_test_set_does_not_leak_into_training():
    train = {_normalize(r["text"]) for r in _rows("train.jsonl")}
    leaked = [r["text"] for r in _rows("test.jsonl") if _normalize(r["text"]) in train]
    assert not leaked
