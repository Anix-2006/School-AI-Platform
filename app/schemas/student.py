from datetime import date
from pydantic import BaseModel


class StudentCreate(BaseModel):
    id: str
    name: str
    grade: str
    section: str | None = None
    age_tier: str
    date_of_birth: date | None = None


class StudentOut(StudentCreate):
    class Config:
        from_attributes = True
