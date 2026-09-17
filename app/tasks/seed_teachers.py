"""Idempotent demo seed for teacher workspace (demo-school). Safe to re-run."""

from datetime import date, time, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models.academic import Attendance, SyllabusProgress
from app.models.student import AgeTier, Student
from app.models.teacher import (
    Teacher,
    TeacherStudentAssignment,
    TeacherSubject,
    TeacherTask,
    TimetableSlot,
)
from app.models.tenant import Tenant

DEFAULT_TEACHER_ID = "t1"
DAY_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday"}


def _get_or_add(db: Session, model, pk: str, **fields):
    row = db.get(model, pk)
    if row:
        return row, False
    row = model(id=pk, **fields)
    db.add(row)
    return row, True


def seed_teacher_demo(db: Session) -> None:
    tenant_id = settings.default_tenant_id
    if not db.get(Tenant, tenant_id):
        db.add(Tenant(id=tenant_id, name="Demo School"))
        db.flush()

    _get_or_add(
        db,
        Teacher,
        DEFAULT_TEACHER_ID,
        tenant_id=tenant_id,
        name="Priya Sharma",
        email="priya.sharma@demo-school.edu",
        role="class_teacher",
        active=True,
    )
    _get_or_add(
        db,
        Teacher,
        "t2",
        tenant_id=tenant_id,
        name="Arjun Rao",
        email="arjun.rao@demo-school.edu",
        role="subject_teacher",
        active=True,
    )

    for sid, name, grade, section, tier in (
        ("s1", "Ananya", "3", "A", AgeTier.PRIMARY),
        ("s2", "Rahul", "3", "A", AgeTier.PRIMARY),
        ("s3", "Meera", "4", "B", AgeTier.PRIMARY),
    ):
        _get_or_add(
            db,
            Student,
            sid,
            tenant_id=tenant_id,
            name=name,
            grade=grade,
            section=section,
            age_tier=tier,
            active=True,
        )

    subjects = (
        ("ts1", DEFAULT_TEACHER_ID, "Math", "3", "A"),
        ("ts2", DEFAULT_TEACHER_ID, "English", "3", "A"),
        ("ts3", "t2", "Science", "3", "A"),
    )
    for pk, tid, subject, grade, section in subjects:
        _get_or_add(
            db,
            TeacherSubject,
            pk,
            teacher_id=tid,
            subject=subject,
            grade=grade,
            section=section,
        )

    assignments = (
        ("ta1", DEFAULT_TEACHER_ID, "s1", "class_teacher", ""),
        ("ta2", DEFAULT_TEACHER_ID, "s2", "class_teacher", ""),
        ("ta3", DEFAULT_TEACHER_ID, "s1", "subject_teacher", "Math"),
        ("ta4", DEFAULT_TEACHER_ID, "s2", "subject_teacher", "Math"),
        ("ta5", DEFAULT_TEACHER_ID, "s1", "subject_teacher", "English"),
        ("ta6", DEFAULT_TEACHER_ID, "s2", "subject_teacher", "English"),
        ("ta7", "t2", "s1", "subject_teacher", "Science"),
        ("ta8", "t2", "s2", "subject_teacher", "Science"),
    )
    for pk, tid, sid, rel, subject in assignments:
        _get_or_add(
            db,
            TeacherStudentAssignment,
            pk,
            teacher_id=tid,
            student_id=sid,
            relationship=rel,
            subject=subject,
        )

    timetable = [
        ("tt-mon-1", 1, 1, time(8, 30), time(9, 10), "Math", "R12"),
        ("tt-mon-2", 1, 2, time(9, 15), time(9, 55), "English", "R12"),
        ("tt-mon-3", 1, 3, time(10, 10), time(10, 50), "Math", "R12"),
        ("tt-tue-1", 2, 1, time(8, 30), time(9, 10), "English", "R12"),
        ("tt-tue-2", 2, 2, time(9, 15), time(9, 55), "Math", "R12"),
        ("tt-wed-1", 3, 1, time(8, 30), time(9, 10), "Math", "R12"),
        ("tt-wed-2", 3, 2, time(9, 15), time(9, 55), "English", "Lab"),
        ("tt-thu-1", 4, 1, time(8, 30), time(9, 10), "Math", "R12"),
        ("tt-thu-2", 4, 3, time(10, 10), time(10, 50), "English", "R12"),
        ("tt-fri-1", 5, 1, time(8, 30), time(9, 10), "English", "R12"),
        ("tt-fri-2", 5, 2, time(9, 15), time(9, 55), "Math", "R12"),
    ]
    for pk, day, period, start, end, subject, room in timetable:
        _get_or_add(
            db,
            TimetableSlot,
            pk,
            teacher_id=DEFAULT_TEACHER_ID,
            day_of_week=day,
            period=period,
            start_time=start,
            end_time=end,
            grade="3",
            section="A",
            subject=subject,
            room=room,
        )

    today = date.today()
    tasks = (
        ("tk1", "Mark FA1 Math worksheets", today + timedelta(days=1), "todo", "Math"),
        ("tk2", "Call Rahul's parent about homework", today, "todo", None),
        ("tk3", "Submit yesterday's attendance", today - timedelta(days=1), "done", None),
        ("tk4", "Prep geometry lesson for Friday", today + timedelta(days=2), "todo", "Math"),
        ("tk5", "Upload class photos to diary", today - timedelta(days=2), "done", "English"),
    )
    for pk, title, due, status, subject in tasks:
        _get_or_add(
            db,
            TeacherTask,
            pk,
            teacher_id=DEFAULT_TEACHER_ID,
            title=title,
            due_date=due,
            status=status,
            related_subject=subject,
        )

    syllabus = (
        ("sy1", "3", "Math", "Fractions", "completed"),
        ("sy2", "3", "Math", "Multiplication", "in_progress"),
        ("sy3", "3", "Math", "Geometry", "not_started"),
        ("sy4", "3", "English", "Nouns", "completed"),
        ("sy5", "3", "English", "Story writing", "in_progress"),
    )
    for pk, grade, subject, chapter, status in syllabus:
        _get_or_add(
            db,
            SyllabusProgress,
            pk,
            tenant_id=tenant_id,
            grade=grade,
            subject=subject,
            chapter=chapter,
            status=status,
            planned_date=None,
            completed_date=today if status == "completed" else None,
        )

    for sid, present in (("s1", True), ("s2", False)):
        att_id = f"att-{sid}-{today.isoformat()}"
        _get_or_add(
            db,
            Attendance,
            att_id,
            student_id=sid,
            date=today,
            present=present,
            note=None,
        )

    db.commit()
