"""WhatsApp inbound webhook. Meta requires a GET verification handshake
plus a POST handler for inbound messages."""

import logging

from fastapi import APIRouter, Body, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.services.chat_service import ChatAccessError, resolve_parent, run_parent_turn
from app.services.excel_store import get_store
from app.services.whatsapp_service import (
    WhatsAppMediaError,
    download_whatsapp_media,
    send_whatsapp_audio,
    send_whatsapp_message,
    upload_media,
)
from app.voice.audio import AudioConversionError, mp3_to_ogg_opus
from app.voice.service import VoiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

# WhatsApp delivers voice notes as OGG/Opus. Other audio types are accepted too.
_AUDIO_TYPES = {
    "audio/ogg",
    "audio/opus",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/aac",
    "audio/amr",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/m4a",
    "audio/x-m4a",
}
VOICE_NOTE_MIME = "audio/ogg; codecs=opus"

UNAVAILABLE_REPLY = (
    "I couldn't answer that voice note just now. Please type your question, or try again in a moment."
)
UNSUPPORTED_REPLY = (
    "I can only use a voice note or an audio file. Please try again, or type your question."
)
TOO_LARGE_REPLY = "That voice note is too large. Please send a shorter one, or type your question."
BUSY_REPLY = (
    "I'm finishing another voice note. Please wait a moment and try again, or type your question."
)


def _too_long_reply() -> str:
    return (
        f"That voice note is longer than {settings.voice_max_seconds:g} seconds. "
        "Please send a shorter one, or type your question."
    )


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

VOICE_NOTE_EXAMPLE = {
    "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "<whatsapp_phone_number_id from GET /auth/demo-token>"},
        "messages": [{
            "from": "<whatsapp_number from GET /guardians>",
            "type": "audio",
            "audio": {"id": "<media id>", "mime_type": "audio/ogg; codecs=opus", "voice": True},
        }],
    }}]}]
}


@router.post("")
async def receive_message(
    request: Request,
    payload: dict = Body(
        ...,
        description="Meta WhatsApp webhook payload (only the fields read here are shown).",
        openapi_examples={
            "text message": {"summary": "Text message from a parent", "value": WEBHOOK_EXAMPLE},
            "voice note": {"summary": "Voice note from a parent", "value": VOICE_NOTE_EXAMPLE},
        },
    ),
):
    # NOTE: real payload parsing depends on Meta's webhook schema -
    # this extracts the common case, harden against malformed/edge payloads
    # before production use.
    parsed = _parse_webhook(payload)
    if parsed is None:
        return {"status": "ignored", "reason": "unsupported payload"}
    phone_number_id, from_number, kind, content = parsed

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

    if kind == "audio":
        return await _receive_voice_note(
            request, tenant["id"], guardian, student, from_number, content
        )

    turn = await run_in_threadpool(
        run_parent_turn,
        tenant["id"],
        guardian,
        student,
        content["text"],
        "whatsapp",
        _whatsapp_thread(guardian),
    )
    sent = await send_whatsapp_message(to=from_number, body=turn["reply"])
    return _reply_response(
        sent, reply=turn["reply"], agent_used=turn.get("agent_used"), audio_produced=False
    )


def _parse_webhook(payload: dict):
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        phone_number_id = value["metadata"]["phone_number_id"]
        message = value["messages"][0]
        from_number = message["from"]
        if message.get("type") == "audio":
            media_id = message["audio"]["id"]
            if not isinstance(media_id, str) or not media_id.strip():
                return None
            return (
                phone_number_id,
                from_number,
                "audio",
                {"media_id": media_id.strip(), "mime_type": message["audio"].get("mime_type")},
            )
        text = message["text"]["body"]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return phone_number_id, from_number, "text", {"text": text}


def _whatsapp_thread(guardian: dict) -> str:
    """One conversation memory per guardian on WhatsApp, for text and voice."""
    return f"{guardian['id']}:whatsapp"


def _audio_mime(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _reply_response(sent: dict, *, reply: str, agent_used, audio_produced: bool) -> dict:
    response = {"status": "ok", "delivery": sent.get("status", "sent")}
    if settings.env == "development":
        # Meta ignores this body. Swagger can show the reply without a WhatsApp account.
        response.update(reply=reply, agent_used=agent_used, audio_produced=audio_produced)
    return response


def _ready_voice(request: Request):
    runtime = getattr(request.app.state, "voice_runtime", None)
    service = getattr(runtime, "service", None)
    if getattr(runtime, "state", None) != "ready" or service is None:
        return None
    return service


async def _receive_voice_note(request, tenant_id, guardian, student, from_number, content) -> dict:
    declared = _audio_mime(content.get("mime_type"))
    if declared not in _AUDIO_TYPES:
        logger.info("WhatsApp voice note from %s rejected: unsupported type %s", guardian["id"], declared or "missing")
        sent = await send_whatsapp_message(to=from_number, body=UNSUPPORTED_REPLY)
        return _reply_response(sent, reply=UNSUPPORTED_REPLY, agent_used=None, audio_produced=False)

    service = _ready_voice(request)
    if service is None:
        logger.info("WhatsApp voice note from %s not answered: voice runtime is not ready", guardian["id"])
        sent = await send_whatsapp_message(to=from_number, body=UNAVAILABLE_REPLY)
        return _reply_response(sent, reply=UNAVAILABLE_REPLY, agent_used=None, audio_produced=False)

    try:
        media = await download_whatsapp_media(content["media_id"], max_bytes=settings.voice_max_bytes)
    except WhatsAppMediaError as exc:
        logger.info("WhatsApp media for %s not downloaded: %s", guardian["id"], exc.detail)
        reply = TOO_LARGE_REPLY if exc.reason == "too_large" else UNAVAILABLE_REPLY
        sent = await send_whatsapp_message(to=from_number, body=reply)
        return _reply_response(sent, reply=reply, agent_used=None, audio_produced=False)

    downloaded_type = _audio_mime(media.mime_type)
    if media.mime_type and downloaded_type not in _AUDIO_TYPES:
        sent = await send_whatsapp_message(to=from_number, body=UNSUPPORTED_REPLY)
        return _reply_response(sent, reply=UNSUPPORTED_REPLY, agent_used=None, audio_produced=False)
    if not media.data:
        sent = await send_whatsapp_message(to=from_number, body=UNSUPPORTED_REPLY)
        return _reply_response(sent, reply=UNSUPPORTED_REPLY, agent_used=None, audio_produced=False)

    # The parent's recording and the generated reply exist only until this returns.
    inbound = bytearray(media.data)
    speech = bytearray()
    ogg = bytearray()
    try:
        try:
            turn = await service.chat(
                tenant_id=tenant_id,
                guardian=guardian,
                student=student,
                audio=bytes(inbound),
                content_type=downloaded_type or declared,
                language=None,
                session_id=None,
                channel="whatsapp",
                thread_id=_whatsapp_thread(guardian),
                retain_audio=False,
            )
        except VoiceError as exc:
            if exc.status_code == 413:
                reply = _too_long_reply()
            elif exc.status_code == 429:
                reply = BUSY_REPLY
            else:
                reply = UNAVAILABLE_REPLY
            logger.info("WhatsApp voice note from %s not answered: %s", guardian["id"], exc.detail)
            sent = await send_whatsapp_message(to=from_number, body=reply)
            return _reply_response(sent, reply=reply, agent_used=None, audio_produced=False)
        except Exception as exc:
            logger.warning("WhatsApp voice note from %s failed: %s", guardian["id"], type(exc).__name__)
            sent = await send_whatsapp_message(to=from_number, body=UNAVAILABLE_REPLY)
            return _reply_response(sent, reply=UNAVAILABLE_REPLY, agent_used=None, audio_produced=False)

        speech.extend(turn.pop("speech", b"") or b"")
        # Text goes out first. A failed voice note must not leave the parent with silence.
        sent = await send_whatsapp_message(to=from_number, body=turn["reply"])
        audio_produced = False
        if speech:
            audio_produced = await _deliver_reply_audio(from_number, bytes(speech), ogg)
        return _reply_response(
            sent,
            reply=turn["reply"],
            agent_used=turn.get("agent_used"),
            audio_produced=audio_produced,
        )
    finally:
        inbound.clear()
        speech.clear()
        ogg.clear()


async def _deliver_reply_audio(to: str, mp3: bytes, sink: bytearray) -> bool:
    """Convert, upload, and send a voice note. Any failure returns False and does not raise."""
    try:
        try:
            converted = await run_in_threadpool(mp3_to_ogg_opus, mp3)
        except AudioConversionError as exc:
            logger.warning("WhatsApp voice reply was not converted: %s", exc)
            return False
        sink.extend(converted)
        uploaded = await upload_media(bytes(sink), VOICE_NOTE_MIME)
        if uploaded.get("status") == "failed":
            logger.warning("WhatsApp voice reply upload failed: %s", uploaded.get("error"))
            return False
        if uploaded.get("status") == "stubbed":
            # Logged locally. There is no Meta media id to send.
            audio_sent = await send_whatsapp_audio(to, None)
        else:
            media_id = uploaded.get("media_id")
            if not media_id:
                logger.warning("WhatsApp voice reply upload failed: missing media id")
                return False
            audio_sent = await send_whatsapp_audio(to, media_id)
        if audio_sent.get("status") == "failed":
            logger.warning("WhatsApp voice reply audio send failed: %s", audio_sent.get("error"))
            return False
        return True
    except Exception as exc:
        logger.warning("WhatsApp voice reply audio was not sent: %s", type(exc).__name__)
        return False
