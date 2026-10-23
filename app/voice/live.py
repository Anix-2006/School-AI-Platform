"""One live browser conversation: VAD, then the existing voice turn."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.core.security import decode_raw_token
from app.services.chat_service import ChatAccessError, resolve_parent
from app.voice.service import VoiceError, audio_owner_key
from app.voice.vad import Pcm16Resampler, UtteranceSegmenter, segmenter_from_settings

logger = logging.getLogger(__name__)


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)
    return target.getvalue()


class LiveConversation:
    """Keep one guardian session and answer each finished utterance in order."""

    def __init__(
        self,
        *,
        send,
        service,
        tenant_id: str,
        guardian: dict,
        student: dict,
        session_id: str | None,
        segmenter: UtteranceSegmenter,
        source_rate: int,
    ):
        self._send = send
        self.service = service
        self.tenant_id = tenant_id
        self.guardian = guardian
        self.student = student
        self.session_id = session_id
        self.segmenter = segmenter
        self.resampler = Pcm16Resampler(source_rate, segmenter.sample_rate)
        self.phase = "listening"
        self.generation = 0
        self.pending: list[bytes] = []
        self._task: asyncio.Task | None = None
        self._state = asyncio.Lock()

    async def accept_audio(self, pcm: bytes) -> None:
        if len(pcm) > settings.voice_max_bytes:
            await self._send(
                {
                    "type": "error",
                    "status": 413,
                    "detail": f"Audio exceeds the {settings.voice_max_bytes} byte limit",
                }
            )
            return
        tick = self.segmenter.push(self.resampler.push(pcm))
        barge = False
        async with self._state:
            if tick.speech_started and self.phase in {"thinking", "speaking"}:
                barge = True
        if barge:
            await self.barge_in()
        start = None
        async with self._state:
            self.pending.extend(tick.utterances)
            if self.phase == "listening" and self.pending:
                start = self.pending.pop(0)
        if start is not None:
            await self._start_turn(start)

    async def playback_done(self) -> None:
        start = None
        async with self._state:
            if self.phase != "speaking":
                return
            self.phase = "listening"
            if self.pending:
                start = self.pending.pop(0)
        if start is not None:
            await self._start_turn(start)
        else:
            await self._send({"type": "status", "state": "listening"})

    async def barge_in(self) -> None:
        async with self._state:
            self.generation += 1
            task = self._task
            self._task = None
            self.pending.clear()
            self.phase = "listening"
        if task is not None and not task.done():
            task.cancel()
        await self._send({"type": "barge_in"})
        await self._send({"type": "status", "state": "listening"})

    async def close(self) -> None:
        async with self._state:
            self.generation += 1
            task = self._task
            self._task = None
            self.pending.clear()
        if task is not None and not task.done():
            task.cancel()

    async def _start_turn(self, pcm: bytes) -> None:
        async with self._state:
            self.generation += 1
            generation = self.generation
            self.phase = "thinking"
            self._task = asyncio.create_task(self._run_turn(generation, pcm))
        await self._send({"type": "status", "state": "thinking"})

    async def _run_turn(self, generation: int, pcm: bytes) -> None:
        streamed = False

        async def on_sentence(index: int, sentence: str, audio: bytes) -> None:
            nonlocal streamed
            async with self._state:
                if generation != self.generation:
                    return
                first = index == 0 and not streamed
                streamed = True
                if first:
                    self.phase = "speaking"
            if generation != self.generation:
                return
            if first:
                await self._send({"type": "status", "state": "speaking"})
            await self._send(
                {
                    "type": "sentence",
                    "index": index,
                    "text": sentence,
                    "audio_base64": _b64(audio),
                }
            )

        try:
            result = await self.service.chat(
                tenant_id=self.tenant_id,
                guardian=self.guardian,
                student=self.student,
                audio=pcm16_to_wav(pcm, self.segmenter.sample_rate),
                content_type="audio/wav",
                language=None,
                session_id=self.session_id,
                lock_language=True,
                on_sentence=on_sentence,
            )
        except asyncio.CancelledError:
            return
        except VoiceError as exc:
            await self._fail_turn(generation, exc.status_code, exc.detail)
            return
        except Exception:
            logger.exception("Live voice turn failed")
            await self._fail_turn(
                generation, 503, "The assistant could not answer. Please try again."
            )
            return
        audio_id = None if streamed else result.get("audio_id")
        speech = (
            self.service.audio_store.take(
                audio_id, audio_owner_key(self.tenant_id, self.guardian["id"])
            )
            if audio_id
            else None
        )
        text_only = not streamed and not speech
        async with self._state:
            if generation != self.generation:
                return
            if text_only:
                self.phase = "listening"
            elif not streamed:
                self.phase = "speaking"
        payload = {
            "type": "reply",
            "transcript": result.get("transcript") or "",
            "reply": result.get("reply") or "",
            "agent_used": result.get("agent_used"),
            "language": result.get("language") or "en",
            "audio_base64": None if streamed else _b64(speech),
        }
        await self._send(payload)
        if text_only:
            await self._send({"type": "status", "state": "listening"})
            return
        async with self._state:
            if generation != self.generation or self.phase != "speaking":
                return
        await self._send({"type": "status", "state": "speaking"})

    async def _fail_turn(self, generation: int, status_code: int, detail: str) -> None:
        async with self._state:
            if generation != self.generation:
                return
            self.phase = "listening"
        await self._send({"type": "error", "status": status_code, "detail": detail})
        await self._send({"type": "status", "state": "listening"})


def _b64(speech: bytes | None) -> str | None:
    if not speech:
        return None
    import base64

    return base64.b64encode(speech).decode("ascii")


def _token(websocket: WebSocket) -> str:
    query = websocket.query_params.get("access_token", "").strip()
    if query:
        return query
    header = websocket.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return ""


def _runtime_ready(websocket: WebSocket):
    runtime = getattr(websocket.app.state, "voice_runtime", None)
    if runtime is None or runtime.state == "disabled":
        return None, (404, "Not found")
    if runtime.state != "ready" or runtime.service is None:
        return None, (503, f"Voice service is {getattr(runtime, 'state', 'unavailable')}")
    return runtime.service, None


async def serve_live(websocket: WebSocket) -> None:
    """Authenticate once, check the guardian once, then stream utterances."""
    await websocket.accept()
    try:
        token = _token(websocket)
        if not token:
            await _error(websocket, 401, "Not authenticated")
            return
        try:
            payload = decode_raw_token(token)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 401)
            detail = getattr(exc, "detail", "Invalid or expired token")
            await _error(websocket, status_code, str(detail))
            return
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            await _error(websocket, 401, "Not authenticated")
            return
        service, problem = _runtime_ready(websocket)
        if problem is not None:
            await _error(websocket, problem[0], problem[1])
            return

        first = await websocket.receive()
        if first.get("type") == "websocket.disconnect":
            return
        raw = first.get("text")
        if not raw:
            await _error(websocket, 422, "Expected a start message")
            return
        try:
            start = json.loads(raw)
        except json.JSONDecodeError:
            await _error(websocket, 422, "Expected a start message")
            return
        if not isinstance(start, dict) or start.get("type") != "start":
            await _error(websocket, 422, "Expected a start message")
            return
        guardian_id = str(start.get("guardian_id") or "").strip()
        if not guardian_id:
            await _error(websocket, 422, "guardian_id is required")
            return
        session_id = str(start.get("session_id") or "").strip() or None
        try:
            source_rate = int(start.get("sample_rate") or settings.voice_vad_sample_rate)
        except (TypeError, ValueError):
            source_rate = settings.voice_vad_sample_rate
        try:
            guardian, student = resolve_parent(tenant_id, guardian_id)
        except ChatAccessError as exc:
            await _error(websocket, exc.status_code, exc.detail)
            return
        try:
            segmenter = segmenter_from_settings()
        except ValueError as exc:
            await _error(websocket, 500, str(exc))
            return

        async def send(payload: dict) -> None:
            await websocket.send_json(payload)

        conversation = LiveConversation(
            send=send,
            service=service,
            tenant_id=tenant_id,
            guardian=guardian,
            student=student,
            session_id=session_id,
            segmenter=segmenter,
            source_rate=source_rate,
        )
        await send(
            {
                "type": "ready",
                "sample_rate": segmenter.sample_rate,
                "silence_ms": segmenter.silence_ms,
                "max_utterance_seconds": settings.voice_max_seconds,
                "sensitivity": segmenter.sensitivity,
            }
        )
        await send({"type": "status", "state": "listening"})
        try:
            while True:
                message = await websocket.receive()
                kind = message.get("type")
                if kind == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await conversation.accept_audio(message["bytes"])
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "status": 422, "detail": "Invalid live message"}
                    )
                    continue
                event = payload.get("type") if isinstance(payload, dict) else None
                if event == "stop":
                    break
                if event == "playback_done":
                    await conversation.playback_done()
                elif event == "barge_in":
                    await conversation.barge_in()
        finally:
            await conversation.close()
    except WebSocketDisconnect:
        return
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


async def _error(websocket: WebSocket, status_code: int, detail: str) -> None:
    await websocket.send_json({"type": "error", "status": status_code, "detail": detail})
