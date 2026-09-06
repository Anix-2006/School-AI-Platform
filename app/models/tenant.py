from sqlalchemy import Column, String
from sqlalchemy.orm import relationship

from app.database import Base


class Tenant(Base):
    """A single school. Every other table carries a tenant_id FK back here —
    this is the row-level isolation boundary. In production Postgres, pair
    this with RLS policies keyed on tenant_id (see app/core/tenancy.py)."""

    __tablename__ = "tenants"

    id = Column(String, primary_key=True)  # slug, e.g. "sunrise-cbse-hyd"
    name = Column(String, nullable=False)
    board = Column(String, default="CBSE")
    whatsapp_phone_number_id = Column(String, nullable=True)

    students = relationship("Student", back_populates="tenant")
