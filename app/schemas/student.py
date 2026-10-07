from datetime import date
from pydantic import BaseModel

AGE_TIERS = ("pre_primary", "primary_lower", "primary")


class StudentCreate(BaseModel):
    id: str
    name: str
    grade: str
    section: str | None = None
    age_tier: str
    date_of_birth: date | None = None


class StudentOut(StudentCreate):
    pass


class GuardianOut(BaseModel):
    id: str
    name: str
    relation: str | None = None
    student_id: str
    student_name: str
    grade: str
    section: str | None = None
    preferred_language: str
    whatsapp_number: str | None = None
    has_consent: bool
