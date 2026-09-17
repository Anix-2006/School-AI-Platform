"""Teacher workspace APIs. Run with: pytest tests/test_teachers.py"""

import os
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///./dev.db")

import pytest
from fastapi.testclient import TestClient

from app.services.excel_teacher_store import DEFAULT_TEACHER_ID, write_demo_workbook


@pytest.fixture
def client(tmp_path, monkeypatch):
    xlsx = tmp_path / "teacher_workspace.xlsx"
    write_demo_workbook(xlsx)
    monkeypatch.setattr("app.services.excel_teacher_store.settings.teacher_excel_path", str(xlsx))
    from app.main import app

    with TestClient(app) as c:
        yield c


def _auth_headers(client):
    token = client.get("/auth/demo-token").json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_teacher_me_and_workspace_lists(client):
    headers = _auth_headers(client)
    me = client.get("/teachers/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == DEFAULT_TEACHER_ID
    assert body["name"]
    assert body["subjects"]

    timetable = client.get("/teachers/me/timetable", headers=headers)
    assert timetable.status_code == 200
    assert len(timetable.json()) >= 1
    assert "day_name" in timetable.json()[0]

    tasks = client.get("/teachers/me/tasks", headers=headers)
    assert tasks.status_code == 200
    assert len(tasks.json()) >= 1

    syllabus = client.get("/teachers/me/syllabus", headers=headers)
    assert syllabus.status_code == 200
    assert any(row["chapter"] for row in syllabus.json())

    students = client.get("/teachers/me/students", headers=headers)
    assert students.status_code == 200
    names = {s["name"] for s in students.json()}
    assert "Ananya" in names
    assert "age_tier" in students.json()[0]


def test_mark_task_complete(client):
    headers = _auth_headers(client)
    patched = client.patch(
        "/teachers/me/tasks/tk1",
        headers=headers,
        json={"status": "done"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "done"


def test_unknown_teacher_is_404(client):
    headers = _auth_headers(client)
    res = client.get("/teachers/me", headers={**headers, "X-Teacher-Id": "no-such-teacher"})
    assert res.status_code == 404


def test_teacher_workspace_page_is_served(client):
    res = client.get("/flows/teacher-workspace")
    assert res.status_code == 200
    assert "Teacher workspace" in res.text
