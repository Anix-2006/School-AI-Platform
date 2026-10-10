"""Tools shared across agents. Each is a plain function decorated for
LangChain tool-calling - bind only the subset each agent actually needs,
don't hand every agent every tool (keeps prompts smaller, reduces
misuse risk)."""

from datetime import date
from langchain_core.tools import tool

from app.database import SessionLocal
from app.models.student import Student, Guardian
from app.models.academic import Attendance, DailyDiary, AssessmentRecord, MilestoneRecord, SyllabusProgress
from app.services.excel_teacher_store import get_store
from app.services.rag_service import search_curriculum

# Same labels as AssessmentRecord.term, in the order a school year runs.
# An unrecognised label never outranks one of these.
_CANONICAL_TERMS = ("FA1", "FA2", "SA1", "SA2")
_TOPIC_FIELDS = ("title", "kind", "status", "description")


@tool
def get_student_profile(student_id: str) -> dict:
    """Fetch a student's basic profile: name, grade, section, age tier."""
    db = SessionLocal()
    try:
        s = db.query(Student).filter(Student.id == student_id).first()
        if not s:
            return {"error": "student not found"}
        return {
            "name": s.name,
            "grade": s.grade,
            "section": s.section,
            "age_tier": s.age_tier.value,
        }
    finally:
        db.close()


@tool
def get_attendance(student_id: str, for_date: str) -> dict:
    """Get attendance status for a student on a given date (YYYY-MM-DD)."""
    db = SessionLocal()
    try:
        rec = (
            db.query(Attendance)
            .filter(Attendance.student_id == student_id, Attendance.date == for_date)
            .first()
        )
        if not rec:
            return {"present": None, "note": "no record"}
        return {"present": rec.present, "note": rec.note}
    finally:
        db.close()


@tool
def get_daily_diary(student_id: str, for_date: str) -> dict:
    """Get the homework diary (grades 1-7) or care log (pre-primary) entry
    for a student on a given date."""
    db = SessionLocal()
    try:
        rec = (
            db.query(DailyDiary)
            .filter(DailyDiary.student_id == student_id, DailyDiary.date == for_date)
            .first()
        )
        if not rec:
            return {"error": "no entry for this date"}
        return {
            "homework": rec.homework,
            "care_notes": rec.care_notes,
            "activity_theme": rec.activity_theme,
        }
    finally:
        db.close()


@tool
def get_assessment_records(student_id: str, term: str) -> list[dict]:
    """Get marks-based assessment records (grades 3-7 FA/SA, grades 1-2
    CCE remarks) for a student for a given term."""
    db = SessionLocal()
    try:
        recs = (
            db.query(AssessmentRecord)
            .filter(AssessmentRecord.student_id == student_id, AssessmentRecord.term == term)
            .all()
        )
        return [
            {
                "subject": r.subject,
                "marks_obtained": r.marks_obtained,
                "marks_total": r.marks_total,
                "remark": r.qualitative_remark,
            }
            for r in recs
        ]
    finally:
        db.close()


@tool
def get_milestone_records(student_id: str, term: str) -> list[dict]:
    """Get pre-primary developmental milestone records for a student
    (no marks - rubric level per domain)."""
    db = SessionLocal()
    try:
        recs = (
            db.query(MilestoneRecord)
            .filter(MilestoneRecord.student_id == student_id, MilestoneRecord.term == term)
            .all()
        )
        return [
            {"domain": r.domain, "rubric_level": r.rubric_level, "note": r.note}
            for r in recs
        ]
    finally:
        db.close()


@tool
def get_syllabus_progress(tenant_id: str, grade: str, subject: str) -> list[dict]:
    """Get chapter-level syllabus completion status for a grade/subject,
    mapped against the CBSE/NCERT chapter list."""
    db = SessionLocal()
    try:
        recs = (
            db.query(SyllabusProgress)
            .filter(
                SyllabusProgress.tenant_id == tenant_id,
                SyllabusProgress.grade == grade,
                SyllabusProgress.subject == subject,
            )
            .all()
        )
        return [{"chapter": r.chapter, "status": r.status} for r in recs]
    finally:
        db.close()


@tool
def search_cbse_curriculum(query: str, grade: str, subject: str) -> str:
    """Search the CBSE/NCERT curriculum RAG index for content relevant to
    a parent's question (e.g. 'what is my child learning in Math this month')."""
    return search_curriculum(query=query, grade=grade, subject=subject)


def _norm(value: str) -> str:
    return (value or "").strip().casefold()


def _same_class(row: dict, grade: str, section: str) -> bool:
    return (row.get("grade") or "").strip() == (grade or "").strip() and (
        (row.get("section") or "").strip() == (section or "").strip()
    )


def _empty_topics(subject: str) -> dict:
    label = (subject or "").strip() or "this subject"
    return {
        "topics": [],
        "note": f"No {label} topics have been entered for this class yet.",
    }


def _latest_term(rows: list[dict]) -> str:
    """Latest academic term among rows that already match class and subject.

    FA1, FA2, SA1, SA2 follow assessment-record order. Any other label keeps
    the order it first appears in the sheet and is used only when none of
    those four terms have a matching row. Topics from other terms are dropped
    by the caller.
    """
    first_seen: list[str] = []
    for row in rows:
        label = (row.get("term") or "").strip()
        if label and label not in first_seen:
            first_seen.append(label)
    for canonical in reversed(_CANONICAL_TERMS):
        for seen in first_seen:
            if _norm(seen) == _norm(canonical):
                return seen
    if not first_seen:
        return ""
    return first_seen[-1]


@tool
def get_term_topics_for_student(student_id: str, subject: str, term: str = "") -> dict:
    """Topics a student's class is being taught in one subject this term,
    plus the subject teacher's name. Uses school-entered rows only."""
    store = get_store()
    student = next((row for row in store.get("students", []) if row.get("id") == student_id), None)
    if not student:
        return {"error": "student not found"}

    grade = student.get("grade") or ""
    section = student.get("section") or ""
    subject_key = _norm(subject)

    # Class subject teachers are the `subjects` rows (grade, section, subject).
    # `assignments` is per student and has no grade or section.
    assignment = next(
        (
            row
            for row in store.get("subjects", [])
            if _same_class(row, grade, section) and _norm(row.get("subject", "")) == subject_key
        ),
        None,
    )
    display_subject = (assignment.get("subject") if assignment else subject).strip()
    if not assignment:
        return _empty_topics(display_subject)

    teacher_id = (assignment.get("teacher_id") or "").strip()
    teacher = next((row for row in store.get("teachers", []) if row.get("id") == teacher_id), None)
    teacher_name = (teacher.get("name") or "").strip() if teacher else ""
    if not teacher_name:
        return _empty_topics(display_subject)

    matched = []
    for row in store.get("teacher_term_topics", []):
        if not _same_class(row, grade, section):
            continue
        if _norm(row.get("subject", "")) != subject_key:
            continue
        row_teacher = (row.get("teacher_id") or "").strip()
        if row_teacher and row_teacher != teacher_id:
            continue
        matched.append(row)
    if not matched:
        return _empty_topics(display_subject)

    requested = (term or "").strip()
    if requested:
        chosen = [row for row in matched if _norm(row.get("term", "")) == _norm(requested)]
        if not chosen:
            return _empty_topics(display_subject)
        term_label = (chosen[0].get("term") or requested).strip()
    else:
        term_label = _latest_term(matched)
        if not term_label:
            return _empty_topics(display_subject)
        chosen = [row for row in matched if _norm(row.get("term", "")) == _norm(term_label)]

    topics = [{field: row.get(field) or "" for field in _TOPIC_FIELDS} for row in chosen]
    subject_label = (chosen[0].get("subject") or display_subject).strip()
    return {
        "subject": subject_label,
        "term": term_label,
        "teacher": teacher_name,
        "topics": topics,
    }


DAILY_UPDATE_TOOLS = [get_student_profile, get_attendance, get_daily_diary]
ACADEMIC_TOOLS = [get_student_profile, get_assessment_records, get_milestone_records, get_syllabus_progress]
COMMUNICATION_TOOLS = [get_student_profile, get_attendance, get_daily_diary, search_cbse_curriculum]
INSIGHT_TOOLS = [get_attendance, get_assessment_records]
PARENT_TEACHER_TOOLS = [*COMMUNICATION_TOOLS, get_term_topics_for_student]
