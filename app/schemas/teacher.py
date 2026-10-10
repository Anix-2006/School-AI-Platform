from datetime import date, time

from pydantic import BaseModel, ConfigDict, Field


class TeacherSubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subject: str
    grade: str
    section: str


class TeacherListItem(BaseModel):
    id: str
    name: str


class TeacherMeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str | None = None
    role: str
    class_teacher_of: list[str] = []
    subjects: list[TeacherSubjectOut] = []


class TimetableSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    day_of_week: int
    day_name: str
    period: int
    start_time: time | None = None
    end_time: time | None = None
    grade: str
    section: str
    subject: str
    room: str | None = None


class TeacherTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    due_date: date | None = None
    status: str
    related_subject: str | None = None


class TeacherTaskPatch(BaseModel):
    status: str = Field(..., description="todo or done")


class SyllabusRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    grade: str
    subject: str
    chapter: str
    status: str
    planned_date: date | None = None
    completed_date: date | None = None


class TeacherStudentOut(BaseModel):
    id: str
    name: str
    grade: str
    section: str | None = None
    age_tier: str
    present_today: bool | None = None
