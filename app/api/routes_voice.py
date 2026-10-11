from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header

from app.config import settings
from app.core.security import decode_token
from app.core.tenancy import get_current_tenant
from app.services.chat_service import ChatAccessError, resolve_parent
from app.voice.audio import AudioValidationError, validate_audio
from app.voice.service import VoiceError, audio_owner_key

Language = Literal["en", "hi", "te"]


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    language: Language = "en"


class VoiceTimings(BaseModel):
    stt: int
    llm: int
    tts: int
    total: int
    queue: int | None = None


class VoiceChatResponse(BaseModel):
    transcript: str
    reply: str
    agent_used: str | None
    language: Language
    audio_id: str | None
    timings: VoiceTimings


VOICE_CHAT_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["audio", "guardian_id", "student_id"],
                    "properties": {
                        "audio": {"type": "string", "format": "binary"},
                        "guardian_id": {"type": "string"},
                        "student_id": {"type": "string"},
                        "language": {
                            "type": "string",
                            "enum": ["en", "hi", "te"],
                        },
                        "session_id": {"type": "string"},
                    },
                }
            }
        },
    }
}


def _runtime(request: Request):
    runtime = getattr(request.app.state, "voice_runtime", None)
    if runtime is None:
        raise HTTPException(503, "Voice service is unavailable")
    return runtime


def _voice_feature(request: Request):
    runtime = getattr(request.app.state, "voice_runtime", None)
    if runtime is None or runtime.state == "disabled":
        raise HTTPException(404, "Not found")
    return runtime


router = APIRouter(
    prefix="/voice", tags=["voice"], dependencies=[Depends(_voice_feature)]
)


def _service(request: Request):
    runtime = _runtime(request)
    if runtime.state != "ready" or runtime.service is None:
        raise HTTPException(503, f"Voice service is {runtime.state}")
    return runtime.service


@router.get("/status")
async def voice_status(request: Request, _tenant: str = Depends(get_current_tenant)):
    return _runtime(request).status()


@router.post(
    "/chat",
    response_model=VoiceChatResponse,
    openapi_extra=VOICE_CHAT_REQUEST_BODY,
)
async def voice_chat(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
):
    service = _service(request)
    fields, audio_bytes, content_type = await _read_voice_multipart(request)
    guardian_id = fields.get("guardian_id", "").strip()
    student_id = fields.get("student_id", "").strip()
    language = fields.get("language") or None
    session_id = fields.get("session_id") or None
    if not guardian_id or not student_id:
        raise HTTPException(422, "guardian_id and student_id are required")
    if language not in {None, "en", "hi", "te"}:
        raise HTTPException(422, "language must be en, hi, or te")

    # Authorization, consent, and linkage deliberately happen before STT.
    try:
        guardian, student = resolve_parent(tenant_id, guardian_id, student_id)
    except ChatAccessError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    try:
        validate_audio(
            audio_bytes,
            content_type,
            max_bytes=settings.voice_max_bytes,
            max_seconds=settings.voice_max_seconds,
        )
        return await service.chat(
            tenant_id=tenant_id,
            guardian=guardian,
            student=student,
            audio=audio_bytes,
            content_type=content_type,
            language=language,
            session_id=session_id,
        )
    except (AudioValidationError, VoiceError) as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.get("/audio/{audio_id}")
async def voice_audio(
    audio_id: str,
    request: Request,
    guardian_id: str = Query(min_length=1),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _service(request)
    try:
        resolve_parent(tenant_id, guardian_id)
    except ChatAccessError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    audio = service.audio_store.take(audio_id, audio_owner_key(tenant_id, guardian_id))
    if audio is None:
        raise HTTPException(404, "Audio not found")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/speak")
async def voice_speak(
    body: SpeakRequest,
    request: Request,
    payload: dict = Depends(decode_token),
):
    service = _service(request)
    # Demo tokens identify a tenant, not a guardian. Keep this endpoint
    # authenticated and rate-limit that tenant; audio ownership is enforced
    # separately by guardian_id on generated chat audio.
    owner = str(payload.get("guardian_id") or payload.get("tenant_id") or "authenticated")
    try:
        audio = await service.speak(body.text, body.language, owner)
    except VoiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


async def _read_voice_multipart(
    request: Request,
) -> tuple[dict[str, str], bytes, str]:
    content_type = request.headers.get("content-type", "")
    media_type, options = parse_options_header(content_type.encode("latin-1"))
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise HTTPException(415, "Content-Type must be multipart/form-data")

    parts: list[tuple[dict[bytes, bytes], bytes]] = []
    headers: dict[bytes, bytes] = {}
    part_data = bytearray()
    header_name = bytearray()
    header_value = bytearray()

    def on_part_begin() -> None:
        nonlocal headers, part_data
        headers = {}
        part_data = bytearray()

    def on_header_field(data: bytes, start: int, end: int) -> None:
        header_name.extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        header_value.extend(data[start:end])

    def on_header_end() -> None:
        headers[bytes(header_name).lower()] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def on_part_data(data: bytes, start: int, end: int) -> None:
        part_data.extend(data[start:end])

    def on_part_end() -> None:
        parts.append((headers.copy(), bytes(part_data)))

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    max_total = settings.voice_max_bytes + 64 * 1024
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_total:
                raise HTTPException(
                    413, f"Multipart body exceeds the {max_total} byte limit"
                )
            parser.write(chunk)
        parser.finalize()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Invalid multipart body") from None

    fields: dict[str, str] = {}
    audio_bytes: bytes | None = None
    audio_type = "application/octet-stream"
    for part_headers, data in parts:
        _disposition, disposition = parse_options_header(
            part_headers.get(b"content-disposition", b"")
        )
        name = disposition.get(b"name", b"").decode("utf-8", errors="replace")
        if name == "audio":
            audio_bytes = data
            audio_type = part_headers.get(
                b"content-type", b"application/octet-stream"
            ).decode("latin-1")
        elif name:
            if len(data) > 4096:
                raise HTTPException(422, f"{name} is too long")
            try:
                fields[name] = data.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(422, f"Invalid {name} field") from None
    if audio_bytes is None:
        raise HTTPException(422, "audio is required")
    return fields, audio_bytes, audio_type
