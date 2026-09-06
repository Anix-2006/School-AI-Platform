"""Tools shared across agents. Each is a plain function decorated for
LangChain tool-calling - bind only the subset each agent actually needs,
don't hand every agent every tool (keeps prompts smaller, reduces
misuse risk)."""

from datetime import date
from langchain_core.tools import tool

from app.database import SessionLocal
from app.models.student import Student, Guardian
from app.models.academic import Attendance, DailyDiary, AssessmentRecord, MilestoneRecord, SyllabusProgress
from app.services.rag_service import search_curriculum


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


DAILY_UPDATE_TOOLS = [get_student_profile, get_attendance, get_daily_diary]
ACADEMIC_TOOLS = [get_student_profile, get_assessment_records, get_milestone_records, get_syllabus_progress]
COMMUNICATION_TOOLS = [get_student_profile, get_attendance, get_daily_diary, search_cbse_curriculum]
INSIGHT_TOOLS = [get_attendance, get_assessment_records]
