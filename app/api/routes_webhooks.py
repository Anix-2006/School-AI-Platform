"""WhatsApp inbound webhook. Meta requires a GET verification handshake
plus a POST handler for inbound messages."""

import logging

from fastapi import APIRouter, Body, Query, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.services.chat_service import ChatAccessError, resolve_parent, run_parent_turn
from app.services.excel_store import get_store
from app.services.whatsapp_service import (
    WhatsAppMediaError,
    download_whatsapp_media,
    send_whatsapp_message,
)
from app.voice.audio import AudioToolError, InvalidAudioError, audio_duration, to_whisper_wav
from app.voice.service import VoiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

# Audio formats WhatsApp delivers that ffmpeg decodes; voice notes are OGG/Opus.
WHATSAPP_AUDIO_TYPES = {"audio/ogg", "audio/opus", "audio/mpeg", "audio/mp4", "audio/aac", "audio/amr"}

UNSUPPORTED_REPLY = (
    "Sorry, I couldn't process that voice note. Please send an audio message in a supported format."
)
TOO_LARGE_REPLY = "Sorry, that voice note is too large. Please send a shorter one."
UNAVAILABLE_REPLY = (
    "Sorry, voice-note processing is temporarily unavailable. Please try again later."
)


def too_long_reply() -> str:
    return (
        "Your voice note is too long. Please send one that is "
        f"{settings.voice_max_seconds:g} seconds or shorter."
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
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        phone_number_id = value["metadata"]["phone_number_id"]
        message = value["messages"][0]
        from_number = message["from"]
        if message.get("type") == "audio":
            text = None
            media_id = message["audio"]["id"]
            declared_type = message["audio"].get("mime_type")
            if not isinstance(media_id, str) or not media_id.strip():
                raise KeyError("audio.id")
        else:
            text = message["text"]["body"]
    except (KeyError, IndexError, TypeError, AttributeError):
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

    if text is None:
        try:
            turn = await _voice_note_turn(request, tenant["id"], guardian, student, media_id, declared_type)
        except VoiceNoteRejected as rejected:
            logger.info("WhatsApp voice note from %s not answered: %s", guardian["id"], rejected.reason)
            sent = await send_whatsapp_message(to=from_number, body=rejected.reply)
            return {"status": "rejected", "reason": rejected.reason, "delivery": sent.get("status", "sent")}
    else:
        turn = await run_in_threadpool(
            run_parent_turn, tenant["id"], guardian, student, text, "whatsapp", whatsapp_thread_id(guardian)
        )
    sent = await send_whatsapp_message(to=from_number, body=turn["reply"])
    response = {"status": "ok", "delivery": sent.get("status", "sent")}
    if settings.env == "development":
        # Lets the demo WhatsApp page show the reply; Meta ignores the body.
        response.update(reply=turn["reply"], agent_used=turn["agent_used"])
    return response


def whatsapp_thread_id(guardian: dict) -> str:
    """One conversation memory per guardian on WhatsApp, for text and voice."""
    return f"{guardian['id']}:whatsapp"


class VoiceNoteRejected(Exception):
    def __init__(self, reason: str, reply: str):
        super().__init__(reason)
        self.reason = reason
        self.reply = reply


def _is_whatsapp_audio(mime_type: str | None) -> bool:
    return (mime_type or "").split(";", 1)[0].strip().lower() in WHATSAPP_AUDIO_TYPES


async def _voice_note_turn(
    request: Request, tenant_id: str, guardian: dict, student: dict, media_id: str, declared_type: str | None
) -> dict:
    """Download, validate and transcribe an authorized guardian's voice note,
    then answer it through the shared voice pipeline. Raises VoiceNoteRejected."""
    runtime = getattr(request.app.state, "voice_runtime", None)
    service = getattr(runtime, "service", None)
    if getattr(runtime, "state", None) != "ready" or service is None:
        raise VoiceNoteRejected("voice unavailable", UNAVAILABLE_REPLY)
    if not _is_whatsapp_audio(declared_type):
        raise VoiceNoteRejected("unsupported media type", UNSUPPORTED_REPLY)

    max_bytes = settings.voice_max_bytes
    try:
        media = await download_whatsapp_media(media_id, max_bytes=max_bytes)
    except WhatsAppMediaError as exc:
        logger.info("WhatsApp media for %s not downloaded: %s", guardian["id"], exc.detail)
        if exc.reason == "too_large":
            raise VoiceNoteRejected("too large", TOO_LARGE_REPLY) from None
        raise VoiceNoteRejected("download failed", UNAVAILABLE_REPLY) from None
    if media.mime_type and not _is_whatsapp_audio(media.mime_type):
        raise VoiceNoteRejected("unsupported media type", UNSUPPORTED_REPLY)
    if len(media.data) > max_bytes:
        raise VoiceNoteRejected("too large", TOO_LARGE_REPLY)
    if not media.data:
        raise VoiceNoteRejected("empty audio", UNSUPPORTED_REPLY)

    max_seconds = settings.voice_max_seconds
    try:
        duration = await run_in_threadpool(audio_duration, media.data, limit=max_seconds)
        if duration > max_seconds:
            raise VoiceNoteRejected("too long", too_long_reply())
        wav = await run_in_threadpool(to_whisper_wav, media.data, max_seconds=max_seconds)
    except InvalidAudioError:
        raise VoiceNoteRejected("undecodable audio", UNSUPPORTED_REPLY) from None
    except AudioToolError as exc:
        logger.warning("WhatsApp voice note conversion unavailable: %s", exc)
        raise VoiceNoteRejected("conversion unavailable", UNAVAILABLE_REPLY) from None

    try:
        return await service.reply_text(
            tenant_id=tenant_id,
            guardian=guardian,
            student=student,
            audio=wav,
            content_type="audio/wav",
            channel="whatsapp",
            thread_id=whatsapp_thread_id(guardian),
        )
    except VoiceError as exc:
        reply = too_long_reply() if exc.status_code == 413 else UNAVAILABLE_REPLY
        raise VoiceNoteRejected(f"voice error {exc.status_code}", reply) from None
    except Exception as exc:  # noqa: BLE001 - never surface internals to the parent
        logger.warning("WhatsApp voice note processing failed: %s", type(exc).__name__)
        raise VoiceNoteRejected("processing failed", UNAVAILABLE_REPLY) from None
