"""WhatsApp inbound webhook. Meta requires a GET verification handshake
plus a POST handler for inbound messages."""

import logging

from fastapi import APIRouter, Request, Query, HTTPException
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.services.chat_service import ChatAccessError, resolve_parent, run_parent_turn
from app.services.excel_store import get_store
from app.services.whatsapp_service import send_whatsapp_message

logger = logging.getLogger(__name__)

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
        value = payload["entry"][0]["changes"][0]["value"]
        phone_number_id = value["metadata"]["phone_number_id"]
        message = value["messages"][0]
        from_number = message["from"]
        text = message["text"]["body"]
    except (KeyError, IndexError, TypeError):
        return {"status": "ignored", "reason": "unsupported payload"}

    store = get_store()
    tenant = store.tenant_for_phone_number_id(phone_number_id)
    if not tenant:
        return {"status": "ignored", "reason": "unknown business number"}
    guardian = store.guardian_by_whatsapp(from_number, tenant["id"])
    if not guardian:
        return {"status": "ignored", "reason": "sender is not a registered guardian"}
    try:
        guardian, student = resolve_parent(tenant["id"], guardian["id"])
    except ChatAccessError as exc:
        logger.info("WhatsApp message from %s not answered: %s", guardian["id"], exc.detail)
        return {"status": "ignored", "reason": "no consent" if exc.status_code == 403 else "unknown guardian"}

    turn = await run_in_threadpool(
        run_parent_turn, tenant["id"], guardian, student, text, "whatsapp", f"{guardian['id']}:whatsapp"
    )
    sent = await send_whatsapp_message(to=from_number, body=turn["reply"])
    response = {"status": "ok", "delivery": sent.get("status", "sent")}
    if settings.env == "development":
        # Lets the demo WhatsApp page show the reply; Meta ignores the body.
        response.update(reply=turn["reply"], agent_used=turn["agent_used"])
    return response
