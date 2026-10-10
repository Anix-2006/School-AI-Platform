"""Train the parent-message intent classifier.

Embeds the labeled messages in data/intent/train.jsonl, picks the logistic
regression regularization strength by cross-validation, reports accuracy on
data/intent/test.jsonl, and writes the weights to
app/agents/intent_model.json. Labels follow the route definitions in
app/agents/orchestrator.py (ROUTER_PROMPT).

Needs OPENAI_API_KEY and requirements-dev.txt. From the repo root:
    python scripts/train_intent_classifier.py
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_validate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.intent_classifier import MODEL_PATH, IntentClassifier, embed_texts  # noqa: E402
from app.agents.orchestrator import ROUTE_TO_NODE  # noqa: E402

EMBEDDING_MODEL = "text-embedding-3-small"
DATA_DIR = ROOT / "data" / "intent"
C_GRID = [1, 3, 10, 30, 100, 300]
THRESHOLDS = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def load(name: str) -> list[dict]:
    path = DATA_DIR / name
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    unknown = {r["intent"] for r in rows} - set(ROUTE_TO_NODE)
    if unknown:
        raise SystemExit(f"{path}: unknown intents {sorted(unknown)}")
    return rows


def main() -> None:
    train, test = load("train.jsonl"), load("test.jsonl")
    print(f"train: {len(train)} {dict(Counter(r['intent'] for r in train))}")
    print(f"test:  {len(test)} {dict(Counter(r['intent'] for r in test))}")

    print(f"embedding with {EMBEDDING_MODEL}...")
    x_train = embed_texts([r["text"] for r in train], EMBEDDING_MODEL)
    x_test = embed_texts([r["text"] for r in test], EMBEDDING_MODEL)
    y_train = [r["intent"] for r in train]
    y_test = [r["intent"] for r in test]

    # Pick C by log-loss, not accuracy: the router thresholds on confidence,
    # so probabilities need to be calibrated, not just ranked correctly.
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    cv = {
        c: cross_validate(
            LogisticRegression(C=c, max_iter=5000),
            x_train,
            y_train,
            cv=folds,
            scoring=("accuracy", "neg_log_loss"),
        )
        for c in C_GRID
    }
    best_c = max(C_GRID, key=lambda c: cv[c]["test_neg_log_loss"].mean())
    print("\ncross-validation by C (accuracy / log-loss, lower log-loss is better):")
    for c in C_GRID:
        acc = cv[c]["test_accuracy"].mean()
        loss = -cv[c]["test_neg_log_loss"].mean()
        print(f"  C={c:<4} {acc:.3f} / {loss:.3f}{'  <- chosen' if c == best_c else ''}")

    model = LogisticRegression(C=best_c, max_iter=5000).fit(x_train, y_train)
    labels = [str(label) for label in model.classes_]

    classifier = IntentClassifier(
        labels=labels,
        coef=model.coef_,
        intercept=model.intercept_,
        embedding_model=EMBEDDING_MODEL,
    )
    probs = classifier.predict_proba(x_test)
    if not np.allclose(probs, model.predict_proba(x_test), atol=1e-6):
        raise SystemExit("numpy scoring does not match scikit-learn; not writing the model")

    predicted = [labels[i] for i in probs.argmax(axis=1)]
    confidence = probs.max(axis=1)
    test_accuracy = float(np.mean([p == y for p, y in zip(predicted, y_test)]))

    print(f"\ntest accuracy: {test_accuracy:.3f}\n")
    print(classification_report(y_test, predicted, labels=labels, zero_division=0))
    print("confusion matrix (rows = true, cols = predicted):")
    print("  " + "  ".join(f"{label[:8]:>8}" for label in labels))
    for label, row in zip(labels, confusion_matrix(y_test, predicted, labels=labels)):
        print(f"  {label[:8]:>8} " + "  ".join(f"{n:>8}" for n in row))

    print("\nconfidence threshold -> share handled by classifier / accuracy on those:")
    for t in THRESHOLDS:
        kept = confidence >= t
        acc = np.mean([p == y for p, y, k in zip(predicted, y_test, kept) if k]) if kept.any() else float("nan")
        print(f"  {t:.1f}: {kept.mean():.0%} / {acc:.3f}")

    mistakes = [(r["text"], r["intent"], p, c) for r, p, c in zip(test, predicted, confidence) if p != r["intent"]]
    if mistakes:
        print("\nmisclassified test messages:")
        for text, true, pred, conf in mistakes:
            print(f"  [{true} -> {pred} @ {conf:.2f}] {text}")

    MODEL_PATH.write_text(
        json.dumps(
            {
                "embedding_model": EMBEDDING_MODEL,
                "labels": labels,
                "coef": np.round(model.coef_, 6).tolist(),
                "intercept": np.round(model.intercept_, 6).tolist(),
                "C": best_c,
                "train_size": len(train),
                "test_accuracy": round(test_accuracy, 4),
                "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {MODEL_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
