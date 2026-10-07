from fastapi import APIRouter, Depends, HTTPException

from app.schemas.chat import ChatRequest, ChatResponse
from app.core.tenancy import get_current_tenant
from app.services.chat_service import ChatAccessError, resolve_parent, run_parent_turn

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, tenant_id: str = Depends(get_current_tenant)):
    """Single turn through the orchestrator. The student and language come
    from the guardian's row; memory is per guardian (and session_id)."""
    try:
        guardian, student = resolve_parent(tenant_id, req.guardian_id, req.student_id)
    except ChatAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    thread_id = f"{guardian['id']}:{req.session_id}" if req.session_id else guardian["id"]
    return ChatResponse(**run_parent_turn(tenant_id, guardian, student, req.message, "app", thread_id))
