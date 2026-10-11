"""WhatsApp inbound webhook. Meta requires a GET verification handshake
plus a POST handler for inbound messages."""

import asyncio
import logging
import threading

from fastapi import APIRouter, Body, Query, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.services.chat_service import ChatAccessError, resolve_parent, run_parent_turn
from app.services.excel_store import get_store
from app.services.whatsapp_service import download_whatsapp_media, send_whatsapp_message
from app.voice.audio import AudioValidationError, validate_audio
from app.voice.service import VoiceError

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


WEBHOOK_EXAMPLE = {
    "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "<whatsapp_phone_number_id from GET /auth/demo-token>"},
        "messages": [{
            "from": "<whatsapp_number from GET /guardians>",
            "type": "text",
            "text": {"body": "Was my child present today?"},
        }],
    }}]}]
}


@router.post("")
async def receive_message(
    request: Request,
    payload: dict = Body(
        ...,
        description="Meta WhatsApp webhook payload (only the fields read here are shown).",
        openapi_examples={"text message": {"summary": "Text message from a parent", "value": WEBHOOK_EXAMPLE}},
    ),
):
    # NOTE: real payload parsing depends on Meta's webhook schema -
    # this extracts the common case, harden against malformed/edge payloads
    # before production use.
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        phone_number_id = value["metadata"]["phone_number_id"]
        message = value["messages"][0]
        from_number = message["from"]
        msg_type = message.get("type") or "text"
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

    if msg_type in {"audio", "voice"}:
        audio_obj = message.get("audio") or message.get("voice") or {}
        media_id = audio_obj.get("id")
        mime_type = str(audio_obj.get("mime_type") or "audio/ogg").split(";")[0].strip()
        if not media_id:
            return {"status": "ignored", "reason": "unsupported payload"}
        runtime = getattr(request.app.state, "voice_runtime", None)
        _schedule(
            _handle_voice_note(
                runtime,
                tenant["id"],
                guardian,
                student,
                from_number,
                media_id,
                mime_type,
            )
        )
        return {"status": "accepted"}

    try:
        text = message["text"]["body"]
    except (KeyError, TypeError):
        return {"status": "ignored", "reason": "unsupported payload"}

    turn = await run_in_threadpool(
        run_parent_turn, tenant["id"], guardian, student, text, "whatsapp", f"{guardian['id']}:whatsapp"
    )
    sent = await send_whatsapp_message(to=from_number, body=turn["reply"])
    response = {"status": "ok", "delivery": sent.get("status", "sent")}
    if settings.env == "development":
        # Lets the demo WhatsApp page show the reply; Meta ignores the body.
        response.update(reply=turn["reply"], agent_used=turn["agent_used"])
    return response


def _schedule(work) -> None:
    # A thread (not the request event loop) so Meta gets an ACK while Whisper runs.
    def runner() -> None:
        try:
            asyncio.run(work)
        except Exception:
            logger.exception("WhatsApp voice note background task failed")

    threading.Thread(target=runner, name="whatsapp-voice-note", daemon=True).start()


async def _handle_voice_note(
    runtime,
    tenant_id: str,
    guardian: dict,
    student: dict,
    from_number: str,
    media_id: str,
    mime_type: str,
) -> None:
    try:
        audio_bytes, downloaded_type = await download_whatsapp_media(media_id)
        content_type = downloaded_type.split(";")[0].strip() or mime_type
        validate_audio(
            audio_bytes,
            content_type,
            max_bytes=settings.voice_max_bytes,
            max_seconds=settings.voice_max_seconds,
        )
        service = getattr(runtime, "service", None) if runtime is not None else None
        if runtime is None or getattr(runtime, "state", None) != "ready" or service is None:
            await send_whatsapp_message(
                to=from_number,
                body="Voice notes are not available right now. Please send your question as text.",
            )
            return
        turn = await service.chat(
            tenant_id=tenant_id,
            guardian=guardian,
            student=student,
            audio=audio_bytes,
            content_type=content_type,
            language=None,
            session_id="whatsapp",
        )
        await send_whatsapp_message(to=from_number, body=turn["reply"])
    except (AudioValidationError, VoiceError) as exc:
        logger.info("WhatsApp voice note from %s rejected: %s", guardian["id"], exc.detail)
        await send_whatsapp_message(
            to=from_number,
            body="I could not hear a clear question. Please try again, or send it as text.",
        )
    except Exception:
        logger.exception("WhatsApp voice note from %s failed", guardian["id"])
