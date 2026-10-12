from fastapi import APIRouter, Depends, HTTPException

from app.schemas.chat import ChatRequest, ChatResponse
from app.core.tenancy import get_current_tenant
from app.services.chat_service import ChatAccessError, handle_parent_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, tenant_id: str = Depends(get_current_tenant)):
    """Single turn through the orchestrator. The student and language come
    from the guardian's row; memory is per guardian (and session_id)."""
    try:
        result = handle_parent_message(
            tenant_id,
            req.guardian_id,
            req.message,
            student_id=req.student_id,
            session_id=req.session_id,
            channel="app",
        )
    except ChatAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return ChatResponse(**result)
