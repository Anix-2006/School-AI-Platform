"""WhatsApp inbound webhook. Meta requires a GET verification handshake
plus a POST handler for inbound messages."""

from fastapi import APIRouter, Request, Query, HTTPException
from langchain_core.messages import HumanMessage

from app.config import settings
from app.agents.orchestrator import orchestrator_graph
from app.services.whatsapp_service import send_whatsapp_message

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])


@router.get("")
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="verification failed")


@router.post("")
async def receive_message(request: Request):
    payload = await request.json()
    # NOTE: real payload parsing depends on Meta's webhook schema -
    # this extracts the common case, harden against malformed/edge payloads
    # before production use.
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        message = entry["messages"][0]
        from_number = message["from"]
        text = message["text"]["body"]
    except (KeyError, IndexError):
        return {"status": "ignored"}

    config = {"configurable": {"thread_id": from_number}}
    initial_state = {
        "messages": [HumanMessage(content=text)],
        "tenant_id": settings.default_tenant_id,  # look up by phone_number_id in production
        "student_id": None,
        "guardian_id": from_number,
        "language": "en",
        "age_tier": None,
        "intent": None,
        "route_to": None,
    }
    result = orchestrator_graph.invoke(initial_state, config=config)
    reply = result["messages"][-1].content
    await send_whatsapp_message(to=from_number, body=reply)
    return {"status": "ok"}
