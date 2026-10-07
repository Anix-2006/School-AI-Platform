"""Tools shared across agents. Each is a plain function decorated for
LangChain tool-calling - bind only the subset each agent actually needs,
don't hand every agent every tool (keeps prompts smaller, reduces
misuse risk). All data comes from the workbook via get_store()."""

from datetime import date, timedelta

from langchain_core.tools import tool

from app.services.excel_store import get_store
from app.services.rag_service import search_curriculum
from app.services.text_match import same_text, subject_matches


@tool
def get_student_profile(student_id: str) -> dict:
    """Fetch a student's basic profile: name, grade, section, age tier."""
    s = get_store().student(student_id)
    if not s:
        return {"error": "student not found"}
    return {
        "name": s["name"],
        "grade": s["grade"],
        "section": s.get("section"),
        "age_tier": s["age_tier"],
    }


@tool
def get_attendance(student_id: str, for_date: str) -> dict:
    """Get attendance status for a student on a given date (YYYY-MM-DD)."""
    rec = next(
        (r for r in get_store().find("attendance", student_id=student_id) if r.get("date") == for_date[:10]),
        None,
    )
    if not rec:
        return {"present": None, "note": "no record"}
    return {"present": rec.get("present"), "note": rec.get("note")}


@tool
def get_daily_diary(student_id: str, for_date: str) -> dict:
    """Get the homework diary (grades 1-7) or care log (pre-primary) entry
    for a student on a given date."""
    rec = next(
        (r for r in get_store().find("daily_diary", student_id=student_id) if r.get("date") == for_date[:10]),
        None,
    )
    if not rec:
        return {"error": "no entry for this date"}
    return {
        "homework": rec.get("homework"),
        "care_notes": rec.get("care_notes"),
        "activity_theme": rec.get("activity_theme"),
    }


@tool
def get_assessment_records(student_id: str, term: str = "") -> list[dict]:
    """Get marks-based assessment records (grades 3-7 FA/SA, grades 1-2
    CCE remarks) for a student for a given term (e.g. 'FA1'). Leave term
    empty to get every term."""
    recs = [
        r for r in get_store().find("assessment_records", student_id=student_id)
        if not term or same_text(r.get("term"), term)
    ]
    return [
        {
            "subject": r["subject"],
            "term": r["term"],
            "marks_obtained": r.get("marks_obtained"),
            "marks_total": r.get("marks_total"),
            "remark": r.get("qualitative_remark"),
        }
        for r in recs
    ]


@tool
def get_milestone_records(student_id: str, term: str = "") -> list[dict]:
    """Get pre-primary developmental milestone records for a student
    (no marks - rubric level per domain). Leave term empty for every term."""
    recs = [
        r for r in get_store().find("milestone_records", student_id=student_id)
        if not term or same_text(r.get("term"), term)
    ]
    return [
        {"domain": r["domain"], "rubric_level": r["rubric_level"], "term": r["term"], "note": r.get("note")}
        for r in recs
    ]


@tool
def get_syllabus_progress(tenant_id: str, grade: str, subject: str) -> list[dict]:
    """Get chapter-level syllabus completion status for a grade/subject,
    mapped against the CBSE/NCERT chapter list."""
    recs = [
        r for r in get_store().find("syllabus_progress", tenant_id=tenant_id)
        if same_text(r.get("grade"), grade) and subject_matches(subject, r.get("subject"))
    ]
    recs.sort(key=lambda r: (r.get("planned_date") or "", r.get("chapter") or ""))
    return [
        {"chapter": r["chapter"], "status": r["status"], "planned_date": r.get("planned_date")}
        for r in recs
    ]


@tool
def search_cbse_curriculum(query: str, grade: str, subject: str) -> str:
    """Search the CBSE/NCERT curriculum for content relevant to a parent's
    question (e.g. 'what is my child learning in Math this month')."""
    return search_curriculum(query=query, grade=grade, subject=subject)


@tool
def get_teachers_for_student(student_id: str, subject: str = "") -> dict:
    """List the teachers assigned to a student: whether each is the class
    teacher, and which subjects they teach this student. Pass subject
    (e.g. 'Math') to keep only that subject's teacher; the class teacher is
    always included as the default contact, and a note is added when no
    teacher is assigned for that subject. Returns {"teachers": []} when no
    teachers are assigned yet."""
    store = get_store()
    student = store.student(student_id)
    if not student:
        return {"error": "student not found"}
    teachers_by_id = {t["id"]: t for t in store.teachers(student["tenant_id"])}
    assignments = [
        a for a in store.rows("teacher_assignments")
        if same_text(a.get("grade"), student["grade"])
        and same_text(a.get("section"), student.get("section"))
        and a.get("teacher_id") in teachers_by_id
    ]
    teachers: dict[str, dict] = {}
    for a in sorted(assignments, key=lambda a: (teachers_by_id[a["teacher_id"]]["name"], a.get("subject") or "")):
        is_class_teacher = a.get("role") == "class_teacher"
        if not is_class_teacher and subject and not subject_matches(subject, a.get("subject")):
            continue
        entry = teachers.setdefault(
            a["teacher_id"],
            {"name": teachers_by_id[a["teacher_id"]]["name"], "class_teacher": False, "subjects": []},
        )
        if is_class_teacher:
            entry["class_teacher"] = True
        elif a.get("subject") and a["subject"] not in entry["subjects"]:
            entry["subjects"].append(a["subject"])
    result = {"teachers": list(teachers.values())}
    if subject.strip() and not any(entry["subjects"] for entry in result["teachers"]):
        result["note"] = (
            f"No {subject.strip()} teacher is assigned to this student. "
            "The class teacher does not teach this subject."
        )
    return result


@tool
def get_school_calendar(tenant_id: str, from_date: str = "", to_date: str = "") -> dict:
    """List school holidays, exams, parent-teacher meetings and events
    between two dates (YYYY-MM-DD). Defaults to the next 12 months from
    today. Use for questions like 'when is the annual day' or 'is school
    open on Friday'."""
    start = from_date[:10] or date.today().isoformat()
    end = to_date[:10] or (date.today() + timedelta(days=365)).isoformat()
    events = []
    for r in get_store().find("school_calendar", tenant_id=tenant_id):
        first = r.get("start_date")
        last = r.get("end_date") or first
        if not first or last < start or first > end:
            continue
        events.append({
            "title": r["title"],
            "kind": r.get("kind"),
            "start_date": first,
            "end_date": last,
            "grades": r.get("grades") or "all",
            "description": r.get("description"),
        })
    events.sort(key=lambda e: e["start_date"])
    if not events:
        return {"events": [], "note": f"No school events found between {start} and {end}."}
    return {"events": events}


@tool
def get_school_info(tenant_id: str, topic: str = "") -> dict:
    """Get general school information: timings, office hours and contact,
    fees, transport, uniform. Pass a topic word (e.g. 'fees') to narrow it
    down; every topic is returned when nothing matches."""
    rows = get_store().find("school_info", tenant_id=tenant_id)
    info = [{"topic": r["topic"], "details": r["details"]} for r in rows]
    words = [w for w in topic.lower().replace("_", " ").split() if len(w) > 2]
    matched = [i for i in info if any(w in f"{i['topic']} {i['details']}".lower() for w in words)]
    return {"info": matched or info}


DAILY_UPDATE_TOOLS = [get_student_profile, get_attendance, get_daily_diary]
ACADEMIC_TOOLS = [get_student_profile, get_assessment_records, get_milestone_records, get_syllabus_progress]
COMMUNICATION_TOOLS = [
    get_student_profile,
    get_attendance,
    get_daily_diary,
    search_cbse_curriculum,
    get_school_calendar,
    get_school_info,
]
PARENT_TEACHER_TOOLS = [get_student_profile, get_teachers_for_student]
INSIGHT_TOOLS = [get_attendance, get_assessment_records]
