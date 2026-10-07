"""Teacher workspace data from the workbook: teachers, teacher_assignments,
timetable, teacher_tasks, syllabus_progress, students, attendance."""

from __future__ import annotations

from datetime import date

from app.services.excel_store import get_store
from app.services.text_match import same_text, subject_matches

DAY_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday"}
DAY_NUMBERS = {name[:3].lower(): number for number, name in DAY_NAMES.items()}


def day_number(value) -> int | None:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return DAY_NUMBERS.get(text[:3].lower())


def default_teacher_id(tenant_id: str, configured: str = "") -> str | None:
    store = get_store()
    if configured and store.teacher(configured, tenant_id):
        return configured
    teachers = sorted(store.teachers(tenant_id), key=lambda t: t["id"])
    return teachers[0]["id"] if teachers else None


def list_teachers(tenant_id: str) -> list[dict]:
    return [
        {"id": t["id"], "name": t["name"]}
        for t in sorted(get_store().teachers(tenant_id), key=lambda t: t["name"])
    ]


def _assignments(teacher_id: str) -> list[dict]:
    return get_store().find("teacher_assignments", teacher_id=teacher_id)


def _class_label(a: dict) -> str:
    return f"{a['grade']}-{a['section']}" if a.get("section") else str(a["grade"])


def profile(teacher: dict) -> dict:
    assignments = _assignments(teacher["id"])
    class_teacher_of = sorted({_class_label(a) for a in assignments if a.get("role") == "class_teacher"})
    subjects = sorted(
        (
            {"subject": a["subject"], "grade": a["grade"], "section": a.get("section") or ""}
            for a in assignments if a.get("role") == "subject_teacher" and a.get("subject")
        ),
        key=lambda s: (s["subject"], s["grade"], s["section"]),
    )
    return {
        "id": teacher["id"],
        "name": teacher["name"],
        "email": teacher.get("email"),
        "role": "class_teacher" if class_teacher_of else "subject_teacher",
        "class_teacher_of": class_teacher_of,
        "subjects": subjects,
    }


def timetable(teacher_id: str) -> list[dict]:
    slots = []
    for r in get_store().find("timetable", teacher_id=teacher_id):
        day = day_number(r.get("day_of_week"))
        if day is None:
            continue
        slots.append({
            "id": r["id"],
            "day_of_week": day,
            "day_name": DAY_NAMES.get(day, str(day)),
            "period": int(r["period"]),
            "start_time": r.get("start_time"),
            "end_time": r.get("end_time"),
            "grade": r["grade"],
            "section": r.get("section") or "",
            "subject": r["subject"],
            "room": r.get("room"),
        })
    slots.sort(key=lambda s: (s["day_of_week"], s["period"]))
    return slots


def tasks(teacher_id: str) -> list[dict]:
    rows = [
        {
            "id": r["id"],
            "title": r["title"],
            "due_date": r.get("due_date"),
            "status": r.get("status") or "todo",
            "related_subject": r.get("related_subject"),
        }
        for r in get_store().find("teacher_tasks", teacher_id=teacher_id)
    ]
    rows.sort(key=lambda t: (t["status"] != "todo", t["due_date"] or "9999-12-31"))
    return rows


def set_task_status(teacher_id: str, task_id: str, status: str) -> dict | None:
    store = get_store()
    task = store.get("teacher_tasks", task_id)
    if not task or task.get("teacher_id") != teacher_id:
        return None
    store.update_row("teacher_tasks", task_id, {"status": status})
    return next((t for t in tasks(teacher_id) if t["id"] == task_id), None)


def syllabus(tenant_id: str, teacher_id: str) -> list[dict]:
    pairs = [
        (a["grade"], a["subject"]) for a in _assignments(teacher_id)
        if a.get("role") == "subject_teacher" and a.get("subject")
    ]
    rows = [
        {
            "id": r["id"],
            "grade": r["grade"],
            "subject": r["subject"],
            "chapter": r["chapter"],
            "status": r.get("status") or "not_started",
            "planned_date": r.get("planned_date"),
            "completed_date": r.get("completed_date"),
        }
        for r in get_store().find("syllabus_progress", tenant_id=tenant_id)
        if any(same_text(r.get("grade"), g) and subject_matches(s, r.get("subject")) for g, s in pairs)
    ]
    rows.sort(key=lambda r: (r["subject"], r["grade"], r["planned_date"] or "", r["chapter"]))
    return rows


def students(tenant_id: str, teacher_id: str, today: date | None = None) -> list[dict]:
    store = get_store()
    classes = {(str(a["grade"]).lower(), str(a.get("section") or "").lower()) for a in _assignments(teacher_id)}
    today_iso = (today or date.today()).isoformat()
    present = {r["student_id"]: r.get("present") for r in store.rows("attendance") if r.get("date") == today_iso}
    out = [
        {
            "id": s["id"],
            "name": s["name"],
            "grade": s["grade"],
            "section": s.get("section"),
            "age_tier": s.get("age_tier") or "primary",
            "present_today": present.get(s["id"]),
        }
        for s in store.students(tenant_id)
        if (str(s["grade"]).lower(), str(s.get("section") or "").lower()) in classes
    ]
    out.sort(key=lambda s: (s["grade"], s["name"]))
    return out
