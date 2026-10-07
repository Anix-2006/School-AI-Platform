import os
import shutil
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest

SOURCE_WORKBOOK = Path(__file__).resolve().parent.parent / "data" / "school_data.xlsx"


@pytest.fixture(autouse=True)
def no_intent_classifier(monkeypatch):
    """Keep tests off the embeddings API; tests that need a classifier set
    orchestrator.intent_classifier to a fake themselves."""
    from app.agents import orchestrator

    monkeypatch.setattr(orchestrator, "intent_classifier", None)


@pytest.fixture(autouse=True)
def workbook(tmp_path, monkeypatch):
    """Every test gets its own copy of data/school_data.xlsx, so writes never
    touch the real file. Defaults come from the workbook, not from .env."""
    from app.config import settings

    path = tmp_path / "school_data.xlsx"
    shutil.copy(SOURCE_WORKBOOK, path)
    monkeypatch.setattr(settings, "school_data_path", str(path))
    monkeypatch.setattr(settings, "default_tenant_id", "")
    monkeypatch.setattr(settings, "default_teacher_id", "")
    monkeypatch.setattr(settings, "whatsapp_token", "")
    return path


@pytest.fixture
def store(workbook):
    from app.services.excel_store import get_store

    return get_store()


@pytest.fixture
def client(workbook):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        token = c.get("/auth/demo-token").json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c
