from sqlalchemy import Column, String, ForeignKey, Text, DateTime
from sqlalchemy.sql import func

from app.database import Base


class Conversation(Base):
    """One thread per guardian. LangGraph checkpoints keyed on this id -
    lets the communication agent hold context across turns/days."""

    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    guardian_id = Column(String, ForeignKey("guardians.id"), nullable=False)
    channel = Column(String, default="whatsapp")  # whatsapp / voice / app
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)  # user / assistant / system
    content = Column(Text, nullable=False)
    agent = Column(String, nullable=True)  # which specialist agent produced it
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InsightAlert(Base):
    """Output of the insight agent - attendance/performance risk flags
    surfaced to management before parents ask."""

    __tablename__ = "insight_alerts"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    alert_type = Column(String, nullable=False)  # attendance_drop / grade_decline / etc
    severity = Column(String, default="medium")  # low / medium / high
    detail = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved = Column(String, default="open")  # open / acknowledged / resolved
