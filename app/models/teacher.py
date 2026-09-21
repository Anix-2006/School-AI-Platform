from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship as orm_relationship

from app.database import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    role = Column(String, nullable=False, default="subject_teacher")  # class_teacher / subject_teacher
    active = Column(Boolean, default=True)

    tenant = orm_relationship("Tenant")
    subjects = orm_relationship("TeacherSubject", back_populates="teacher")
    assignments = orm_relationship("TeacherStudentAssignment", back_populates="teacher")
    timetable_slots = orm_relationship("TimetableSlot", back_populates="teacher")
    tasks = orm_relationship("TeacherTask", back_populates="teacher")


class TeacherSubject(Base):
    __tablename__ = "teacher_subjects"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id", "subject", "grade", "section", name="uq_teacher_subject"
        ),
    )

    id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("teachers.id"), nullable=False)
    subject = Column(String, nullable=False)
    grade = Column(String, nullable=False)
    section = Column(String, nullable=False, default="")

    teacher = orm_relationship("Teacher", back_populates="subjects")


class TeacherStudentAssignment(Base):
    __tablename__ = "teacher_student_assignments"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id",
            "student_id",
            "relationship",
            "subject",
            name="uq_teacher_student",
        ),
        Index("ix_teacher_student_student_id", "student_id"),
    )

    id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("teachers.id"), nullable=False)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    relationship = Column(String, nullable=False)  # class_teacher | subject_teacher
    subject = Column(String, nullable=False, default="")

    teacher = orm_relationship("Teacher", back_populates="assignments")
    student = orm_relationship("Student")


class TimetableSlot(Base):
    __tablename__ = "timetable_slots"

    id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("teachers.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 1=Monday … 5=Friday
    period = Column(Integer, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    grade = Column(String, nullable=False)
    section = Column(String, nullable=False, default="")
    subject = Column(String, nullable=False)
    room = Column(String, nullable=True)

    teacher = orm_relationship("Teacher", back_populates="timetable_slots")


class TeacherTask(Base):
    __tablename__ = "teacher_tasks"

    id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("teachers.id"), nullable=False)
    title = Column(String, nullable=False)
    due_date = Column(Date, nullable=True)
    status = Column(String, nullable=False, default="todo")  # todo / done
    related_subject = Column(String, nullable=True)

    teacher = orm_relationship("Teacher", back_populates="tasks")
