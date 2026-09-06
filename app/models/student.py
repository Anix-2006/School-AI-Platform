import enum

from sqlalchemy import Column, String, ForeignKey, Enum, Date, Boolean
from sqlalchemy.orm import relationship

from app.database import Base


class AgeTier(str, enum.Enum):
    PRE_PRIMARY = "pre_primary"      # Nursery, LKG, UKG
    PRIMARY_LOWER = "primary_lower"  # Grades 1-2
    PRIMARY = "primary"              # Grades 3-7


class Student(Base):
    __tablename__ = "students"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    grade = Column(String, nullable=False)  # "Nursery", "LKG", "UKG", "1".."7"
    section = Column(String, nullable=True)
    age_tier = Column(Enum(AgeTier), nullable=False)
    date_of_birth = Column(Date, nullable=True)
    active = Column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="students")
    guardians = relationship("Guardian", back_populates="student")


class Guardian(Base):
    """Parent/guardian contact — this is who the agents actually message."""

    __tablename__ = "guardians"

    id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    name = Column(String, nullable=False)
    relation = Column(String, default="parent")
    whatsapp_number = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    preferred_language = Column(String, default="en")  # en / hi / te
    consent_given_at = Column(Date, nullable=True)  # DPDP: no processing before this is set

    student = relationship("Student", back_populates="guardians")
