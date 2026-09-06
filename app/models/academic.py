from sqlalchemy import Column, String, ForeignKey, Date, Float, Text, Boolean
from sqlalchemy.orm import relationship

from app.database import Base


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    date = Column(Date, nullable=False)
    present = Column(Boolean, nullable=False)
    note = Column(Text, nullable=True)  # e.g. "left early - fever"


class DailyDiary(Base):
    """Covers both homework diary (grades 1-7) and care log (pre-primary)."""

    __tablename__ = "daily_diary"

    id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    date = Column(Date, nullable=False)
    homework = Column(Text, nullable=True)          # grades 1-7
    care_notes = Column(Text, nullable=True)         # nap/meals/mood, pre-primary
    activity_theme = Column(String, nullable=True)   # pre-primary weekly theme
    photo_urls = Column(Text, nullable=True)          # comma-separated S3 keys


class AssessmentRecord(Base):
    """CBSE FA/SA marks for grades 3-7, CCE remarks for grades 1-2.
    Pre-primary uses MilestoneRecord instead."""

    __tablename__ = "assessment_records"

    id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    subject = Column(String, nullable=False)
    term = Column(String, nullable=False)         # "FA1", "FA2", "SA1", "SA2"
    marks_obtained = Column(Float, nullable=True)
    marks_total = Column(Float, nullable=True)
    qualitative_remark = Column(Text, nullable=True)  # grades 1-2 CCE style


class MilestoneRecord(Base):
    """Pre-primary developmental tracking - no marks."""

    __tablename__ = "milestone_records"

    id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    domain = Column(String, nullable=False)  # motor / social-emotional / pre-literacy / pre-numeracy
    rubric_level = Column(String, nullable=False)  # emerging / developing / proficient
    term = Column(String, nullable=False)
    note = Column(Text, nullable=True)


class SyllabusProgress(Base):
    __tablename__ = "syllabus_progress"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    grade = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    chapter = Column(String, nullable=False)  # maps to NCERT chapter list
    status = Column(String, default="not_started")  # not_started / in_progress / completed
    planned_date = Column(Date, nullable=True)
    completed_date = Column(Date, nullable=True)
