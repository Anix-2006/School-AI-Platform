from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    guardian_id: str
    # Optional check: rejected if it isn't the guardian's linked student.
    student_id: str | None = None
    message: str
    session_id: str | None = Field(
        None, description="Separate conversation memory for the same guardian (e.g. one per test)."
    )


class ChatResponse(BaseModel):
    reply: str
    agent_used: str | None = None
    intent_source: str | None = None
    intent_confidence: float | None = None
    student_id: str | None = None
    language: str | None = None
