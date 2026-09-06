from fastapi import APIRouter, Depends
from langchain_core.messages import HumanMessage

from app.schemas.chat import ChatRequest, ChatResponse
from app.agents.orchestrator import orchestrator_graph
from app.agents.tool_loop import extract_text_reply
from app.core.tenancy import get_current_tenant

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, tenant_id: str = Depends(get_current_tenant)):
    """Single turn through the orchestrator. thread_id = guardian_id keeps
    conversation memory per-parent via the LangGraph checkpointer."""
    config = {"configurable": {"thread_id": req.guardian_id}}
    initial_state = {
        "messages": [HumanMessage(content=req.message)],
        "tenant_id": tenant_id,
        "student_id": req.student_id,
        "guardian_id": req.guardian_id,
        "language": req.language,
        "age_tier": None,
        "intent": None,
        "route_to": None,
    }
    result = orchestrator_graph.invoke(initial_state, config=config)
    reply = extract_text_reply(result["messages"])
    return ChatResponse(reply=reply, agent_used=result.get("route_to"))
