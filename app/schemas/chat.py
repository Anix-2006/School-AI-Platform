from pydantic import BaseModel


class ChatRequest(BaseModel):
    guardian_id: str
    student_id: str | None = None
    message: str
    language: str = "en"


class ChatResponse(BaseModel):
    reply: str
    agent_used: str | None = None
