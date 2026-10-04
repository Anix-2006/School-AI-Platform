"""get_teachers_for_student tool. Run with:
pytest tests/test_teacher_lookup_tool.py
Uses an in-memory SQLite database loaded with the teacher demo seed."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents import tools
from app.database import Base
from app.models import academic, conversation, student, teacher, tenant  # noqa: F401
from app.models.teacher import Teacher
from app.tasks.seed_teachers import seed_teacher_demo


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    seed_teacher_demo(db)
    db.close()
    monkeypatch.setattr(tools, "SessionLocal", factory)
    return factory


def lookup(**args):
    return tools.get_teachers_for_student.invoke(args)


def test_returns_class_and_subject_teachers(session_factory):
    assert lookup(student_id="s1") == {
        "teachers": [
            {"name": "Arjun Rao", "class_teacher": False, "subjects": ["Science"]},
            {"name": "Priya Sharma", "class_teacher": True, "subjects": ["English", "Math"]},
        ]
    }


def test_subject_filter_keeps_class_teacher(session_factory):
    assert lookup(student_id="s1", subject="science") == {
        "teachers": [
            {"name": "Arjun Rao", "class_teacher": False, "subjects": ["Science"]},
            {"name": "Priya Sharma", "class_teacher": True, "subjects": []},
        ]
    }


def test_subject_with_no_teacher_returns_class_teacher_and_note(session_factory):
    assert lookup(student_id="s1", subject="Hindi") == {
        "teachers": [{"name": "Priya Sharma", "class_teacher": True, "subjects": []}],
        "note": "No Hindi teacher is assigned to this student. "
        "The class teacher does not teach this subject.",
    }


def test_student_without_assignments_gets_empty_list(session_factory):
    assert lookup(student_id="s3") == {"teachers": []}


def test_unknown_student(session_factory):
    assert lookup(student_id="no-such-student") == {"error": "student not found"}


def test_inactive_teachers_are_hidden(session_factory):
    db = session_factory()
    db.get(Teacher, "t2").active = False
    db.commit()
    db.close()

    names = [t["name"] for t in lookup(student_id="s1")["teachers"]]
    assert names == ["Priya Sharma"]


def test_no_contact_details_are_returned(session_factory):
    for entry in lookup(student_id="s1")["teachers"]:
        assert set(entry) == {"name", "class_teacher", "subjects"}


def test_tool_is_bound_only_to_parent_teacher_agent():
    other_agents = (
        tools.DAILY_UPDATE_TOOLS,
        tools.ACADEMIC_TOOLS,
        tools.COMMUNICATION_TOOLS,
        tools.INSIGHT_TOOLS,
    )
    assert tools.get_teachers_for_student in tools.PARENT_TEACHER_TOOLS
    assert all(tools.get_teachers_for_student not in group for group in other_agents)
