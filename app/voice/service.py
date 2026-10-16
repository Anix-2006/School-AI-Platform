from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

from app.services.chat_service import run_parent_turn
from app.voice.speech_text import clean_for_speech
from app.voice.stt import SttEngine, Transcript
from app.voice.storage import AudioStore
from app.voice.transcription_queue import (
    TranscriptionQueue,
    TranscriptionQueueClosed,
    TranscriptionQueueFull,
)
from app.voice.tts import TtsEngine

logger = logging.getLogger(__name__)

QueueNotifier = Callable[[str, str], Awaitable[None]]


class VoiceError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SlidingRateLimiter:
    def __init__(self):
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, scope: str, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events[(scope, key)]
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


class VoiceService:
    def __init__(
        self,
        stt: SttEngine,
        tts: TtsEngine,
        audio_store: AudioStore,
        *,
        max_parallel: int | None = None,
        max_concurrency: int = 1,
        queue_max: int = 16,
        queue_notify_at: int = 2,
        queue_timeout: float = 2.0,
        guardian_rate: int = 10,
        global_rate: int = 60,
        speak_rate: int = 20,
        max_seconds: float = 30.0,
        queue_notifier: QueueNotifier | None = None,
    ):
        self.stt = stt
        self.tts = tts
        self.audio_store = audio_store
        # Legacy alias: older callers pass max_concurrency only.
        parallel = max_parallel if max_parallel is not None else max_concurrency
        self.max_parallel = max(1, parallel)
        self.queue_max = max(0, queue_max)
        self.queue_notify_at = max(1, queue_notify_at)
        self.queue_timeout = queue_timeout
        self.guardian_rate = guardian_rate
        self.global_rate = global_rate
        self.speak_rate = speak_rate
        self.max_seconds = max_seconds
        self._queue_notifier = queue_notifier
        self._queue = TranscriptionQueue(
            max_parallel=self.max_parallel,
            queue_max=self.queue_max,
            notify_at=self.queue_notify_at,
        )
        self._rates = SlidingRateLimiter()

    def check_rate(self, action: str, guardian_id: str) -> None:
        limit = self.guardian_rate if action == "chat" else self.speak_rate
        if not self._rates.allow(action, guardian_id, limit) or not self._rates.allow(
            "global", "__global__", self.global_rate
        ):
            raise VoiceError(429, "Voice request rate limit exceeded")

    async def chat(
        self,
        *,
        tenant_id: str,
        guardian: dict,
        student: dict,
        audio: bytes,
        content_type: str,
        language: str | None,
        session_id: str | None,
        upload_ms: int = 0,
        decode_ms: int = 0,
    ) -> dict:
        self.check_rate("chat", guardian["id"])
        fallback_language = _voice_language(language, guardian)
        status_language = fallback_language
        queue_notice: str | None = None

        async def on_notify() -> None:
            nonlocal queue_notice
            phrase = queue_status_phrase(status_language)
            queue_notice = phrase
            if self._queue_notifier is not None:
                await self._queue_notifier(guardian["id"], phrase)

        stt_ms = 0

        async def transcribe() -> Transcript:
            nonlocal stt_ms
            prompt = _initial_prompt(student)
            stt_started = time.perf_counter()
            try:
                return await asyncio.to_thread(
                    self.stt.transcribe, audio, content_type, None, prompt
                )
            finally:
                stt_ms = round((time.perf_counter() - stt_started) * 1000)

        try:
            transcript, admission = await self._queue.run(
                transcribe, on_notify=on_notify
            )
        except TranscriptionQueueFull as exc:
            raise VoiceError(429, queue_busy_phrase(status_language)) from exc
        except TranscriptionQueueClosed as exc:
            raise VoiceError(503, queue_busy_phrase(status_language)) from exc

        reply_language = choose_reply_language(transcript)
        if (
            transcript.duration is not None
            and transcript.duration > self.max_seconds
        ):
            raise VoiceError(413, f"Audio exceeds the {self.max_seconds:g} second limit")
        if _is_unusable_transcript(transcript):
            return await self._friendly_retry(
                transcript,
                reply_language if transcript.text.strip() else fallback_language,
                tenant_id,
                guardian["id"],
                student["id"],
                upload_ms,
                decode_ms,
                admission.wait_ms,
                stt_ms,
            )
        thread_id = (
            f"{guardian['id']}:{session_id}" if session_id else guardian["id"]
        )
        llm_started = time.perf_counter()
        result = await asyncio.to_thread(
            run_parent_turn,
            tenant_id,
            guardian,
            student,
            transcript.text,
            "voice",
            thread_id,
            reply_language,
        )
        replied = time.perf_counter()
        spoken_text = clean_for_speech(result["reply"], reply_language)
        try:
            speech = await self.tts.synthesize(spoken_text, reply_language)
        except Exception:
            speech = b""
        finished = time.perf_counter()
        audio_id = (
            self.audio_store.put(audio_owner_key(tenant_id, guardian["id"]), speech)
            if speech
            else None
        )
        router_ms = int(result.get("router_ms") or 0)
        agent_ms = int(result.get("agent_llm_ms") or max(
            0, round((replied - llm_started) * 1000) - router_ms
        ))
        timings_ms = {
            "upload": upload_ms,
            "decode": decode_ms,
            "queue": admission.wait_ms,
            "stt": stt_ms,
            "router": router_ms,
            "agent_llm": agent_ms,
            "tts": round((finished - replied) * 1000),
        }
        timings_ms["total"] = sum(timings_ms.values())
        logger.info(
            "voice_turn guardian_id=%s session_id=%s language=%s "
            "queue_waiting_at_admit=%s timings_ms=%s",
            guardian["id"],
            session_id or "default",
            reply_language,
            admission.waiting,
            timings_ms,
        )
        return {
            **result,
            "language": reply_language,
            "transcript": transcript.text,
            "spoken_text": spoken_text,
            "audio_id": audio_id,
            "tts_degraded": not bool(speech),
            "queue_notice": queue_notice,
            "timings": {
                "queue": timings_ms["queue"],
                "stt": timings_ms["stt"],
                "llm": timings_ms["router"] + timings_ms["agent_llm"],
                "tts": timings_ms["tts"],
                "total": timings_ms["total"],
            },
            "timings_ms": timings_ms,
        }

    async def _friendly_retry(
        self,
        transcript: Transcript,
        language: str,
        tenant_id: str,
        guardian_id: str,
        student_id: str,
        upload_ms: int,
        decode_ms: int,
        wait_ms: int,
        stt_ms: int,
    ) -> dict:
        reply = _RETRY_REPLIES.get(language, _RETRY_REPLIES["en"])
        tts_started = time.perf_counter()
        try:
            speech = await self.tts.synthesize(reply, language)
        except Exception:
            speech = b""
        finished = time.perf_counter()
        timings_ms = {
            "upload": upload_ms,
            "decode": decode_ms,
            "queue": wait_ms,
            "stt": stt_ms,
            "router": 0,
            "agent_llm": 0,
            "tts": round((finished - tts_started) * 1000),
        }
        timings_ms["total"] = sum(timings_ms.values())
        return {
            "reply": reply,
            "transcript": transcript.text,
            "spoken_text": reply,
            "agent_used": None,
            "intent_source": None,
            "intent_confidence": None,
            "student_id": student_id,
            "language": language,
            "audio_id": (
                self.audio_store.put(audio_owner_key(tenant_id, guardian_id), speech)
                if speech
                else None
            ),
            "tts_degraded": not bool(speech),
            "timings": {
                "queue": timings_ms["queue"],
                "stt": timings_ms["stt"],
                "llm": 0,
                "tts": timings_ms["tts"],
                "total": timings_ms["total"],
            },
            "timings_ms": timings_ms,
        }

    async def speak(self, text: str, language: str, owner_id: str) -> bytes:
        self.check_rate("speak", owner_id)
        cleaned = clean_for_speech(text, language)
        if not cleaned:
            raise VoiceError(422, "Text has no speakable content")
        try:
            return await self.tts.synthesize(cleaned, language)
        except ValueError as exc:
            raise VoiceError(422, str(exc)) from None
        except Exception:
            raise VoiceError(503, "Text-to-speech is temporarily unavailable") from None

    def close(self) -> None:
        close = getattr(self.tts, "close", None)
        if close is not None:
            close()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(self._queue.close())
            except RuntimeError:
                pass
        else:
            loop.create_task(self._queue.close())


_RETRY_REPLIES = {
    "en": "I could not hear a clear question. Please try again.",
    "hi": "मुझे सवाल साफ़ सुनाई नहीं दिया। कृपया फिर से बोलें।",
    "te": "నాకు ప్రశ్న స్పష్టంగా వినిపించలేదు. దయచేసి మళ్లీ చెప్పండి.",
}

_QUEUE_STATUS = {
    "en": "Got your message — I'm processing it now.",
    "hi": "आपका संदेश मिल गया — मैं अभी प्रोसेस कर रही हूँ।",
    "te": "మీ సందేశం అందింది — నేను ఇప్పుడు ప్రాసెస్ చేస్తున్నాను.",
}

_QUEUE_BUSY = {
    "en": "The voice service is busy right now. Please try again shortly.",
    "hi": "वॉइस सेवा अभी व्यस्त है। कृपया थोड़ी देर बाद फिर कोशिश करें।",
    "te": "వాయిస్ సేవ ప్రస్తుతం బిజీగా ఉంది. దయచేసి కొద్దిసేపటి తర్వాత మళ్లీ ప్రయత్నించండి.",
}


def queue_status_phrase(language: str) -> str:
    return _QUEUE_STATUS.get(language, _QUEUE_STATUS["en"])


def queue_busy_phrase(language: str) -> str:
    return _QUEUE_BUSY.get(language, _QUEUE_BUSY["en"])


def _initial_prompt(student: dict) -> str:
    name = str(student.get("name") or "").strip()
    subject = f"Student {name}." if name else "The linked student."
    return (
        f"{subject} School terms: attendance, homework, syllabus, "
        "FA1, FA2, formative assessment, summative assessment."
    )


def _voice_language(override: str | None, guardian: dict) -> str:
    if override in {"en", "hi", "te"}:
        return override
    preferred = guardian.get("preferred_language")
    return preferred if preferred in {"en", "hi", "te"} else "en"


def choose_reply_language(transcript: Transcript) -> str:
    """Choose English or Hindi for this turn, independent of profile language."""
    text = transcript.text.strip()
    devanagari = len(re.findall(r"[\u0900-\u097f]", text))
    letters = len(re.findall(r"[^\W\d_]", text, flags=re.UNICODE))
    if transcript.language == "hi" or (letters and devanagari / letters >= 0.15):
        return "hi"
    return "en"


def audio_owner_key(tenant_id: str, guardian_id: str) -> str:
    return f"{tenant_id}\0{guardian_id}"


def _is_unusable_transcript(transcript: Transcript) -> bool:
    if len(transcript.text.strip()) < 2:
        return True
    words = re.findall(r"\w+", transcript.text.casefold())
    if not words or transcript.confidence < 0.15:
        return True
    if len(words) >= 4 and len(set(words)) <= max(1, len(words) // 4):
        return True
    return False
