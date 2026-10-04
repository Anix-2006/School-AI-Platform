"""Embedding-based intent classifier for parent messages.

Messages are embedded with a multilingual OpenAI embedding model and scored
by a multinomial logistic regression trained offline on labeled parent
messages (data/intent/, see scripts/train_intent_classifier.py). Only the
learned weights ship in intent_model.json, so runtime needs numpy but not
scikit-learn. The orchestrator falls back to the LLM router when this model
is missing, errors, or isn't confident."""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
from openai import OpenAI

from app.config import settings

MODEL_PATH = Path(__file__).with_name("intent_model.json")

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    # Short timeout: routing is on the request path and has an LLM fallback.
    return OpenAI(api_key=settings.openai_api_key, timeout=5.0, max_retries=1)


def embed_texts(texts: list[str], model: str) -> np.ndarray:
    response = _client().embeddings.create(model=model, input=texts)
    return np.array([item.embedding for item in response.data], dtype=np.float64)


@dataclass(frozen=True)
class IntentPrediction:
    intent: str
    confidence: float


class IntentClassifier:
    def __init__(
        self,
        labels: list[str],
        coef: np.ndarray,
        intercept: np.ndarray,
        embedding_model: str,
        embed: Callable[[list[str], str], np.ndarray] = embed_texts,
    ):
        if coef.shape[0] != len(labels) or intercept.shape != (len(labels),):
            raise ValueError("intent model weights don't match its labels")
        self.labels = labels
        self.coef = coef
        self.intercept = intercept
        self.embedding_model = embedding_model
        self._embed = embed

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "IntentClassifier | None":
        if not path.is_file():
            logger.warning("intent model not found at %s; using LLM router only", path)
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            labels=data["labels"],
            coef=np.array(data["coef"], dtype=np.float64),
            intercept=np.array(data["intercept"], dtype=np.float64),
            embedding_model=data["embedding_model"],
        )

    def predict_proba(self, vectors: np.ndarray) -> np.ndarray:
        logits = vectors @ self.coef.T + self.intercept
        logits -= logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        return exp / exp.sum(axis=1, keepdims=True)

    def predict(self, text: str) -> IntentPrediction:
        vector = self._embed([text], self.embedding_model)
        probs = self.predict_proba(vector)[0]
        best = int(probs.argmax())
        return IntentPrediction(intent=self.labels[best], confidence=float(probs[best]))
