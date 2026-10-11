from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import defaultdict, deque

from app.services.chat_service import run_parent_turn
from app.voice.speech_text import clean_for_speech
from app.voice.stt import SttEngine, Transcript
from app.voice.storage import AudioStore
from app.voice.tts import TtsEngine

logger = logging.getLogger(__name__)


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
        max_concurrency: int = 1,
        queue_timeout: float = 2.0,
        guardian_rate: int = 10,
        global_rate: int = 60,
        speak_rate: int = 20,
        max_seconds: float = 30.0,
    ):
        self.stt = stt
        self.tts = tts
        self.audio_store = audio_store
        self.queue_timeout = queue_timeout
        self.guardian_rate = guardian_rate
        self.global_rate = global_rate
        self.speak_rate = speak_rate
        self.max_seconds = max_seconds
        self._slots = asyncio.Semaphore(max(1, max_concurrency))
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
        started = time.perf_counter()
        chosen_language = _voice_language(language, guardian)
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout)
        except TimeoutError:
            raise VoiceError(429, "Voice service is busy; please try again") from None
        queued = time.perf_counter()
        try:
            prompt = _initial_prompt(student)
            transcript = await asyncio.to_thread(
                self.stt.transcribe, audio, content_type, chosen_language, prompt
            )
            transcribed = time.perf_counter()
            if (
                transcript.duration is not None
                and transcript.duration > self.max_seconds
            ):
                raise VoiceError(413, f"Audio exceeds the {self.max_seconds:g} second limit")
            if _is_unusable_transcript(transcript):
                return await self._friendly_retry(
                    transcript,
                    chosen_language,
                    tenant_id,
                    guardian["id"],
                    student["id"],
                    started,
                    queued,
                    transcribed,
                    upload_ms,
                    decode_ms,
                )
            thread_id = (
                f"{guardian['id']}:{session_id}" if session_id else guardian["id"]
            )
            result = await asyncio.to_thread(
                run_parent_turn,
                tenant_id,
                guardian,
                student,
                transcript.text,
                "voice",
                thread_id,
                chosen_language,
            )
            replied = time.perf_counter()
            spoken_text = clean_for_speech(result["reply"], chosen_language)
            try:
                speech = await self.tts.synthesize(spoken_text, chosen_language)
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
                0, round((replied - transcribed) * 1000) - router_ms
            ))
            timings_ms = {
                "upload": upload_ms,
                "decode": decode_ms,
                "queue": round((queued - started) * 1000),
                "stt": round((transcribed - queued) * 1000),
                "router": router_ms,
                "agent_llm": agent_ms,
                "tts": round((finished - replied) * 1000),
            }
            timings_ms["total"] = sum(timings_ms.values())
            logger.info(
                "voice_turn guardian_id=%s session_id=%s language=%s timings_ms=%s",
                guardian["id"],
                session_id or "default",
                chosen_language,
                timings_ms,
            )
            return {
                **result,
                "transcript": transcript.text,
                "spoken_text": spoken_text,
                "audio_id": audio_id,
                "tts_degraded": not bool(speech),
                "timings": {
                    "queue": timings_ms["queue"],
                    "stt": timings_ms["stt"],
                    "llm": timings_ms["router"] + timings_ms["agent_llm"],
                    "tts": timings_ms["tts"],
                    "total": timings_ms["total"],
                },
                "timings_ms": timings_ms,
            }
        finally:
            self._slots.release()

    async def _friendly_retry(
        self,
        transcript: Transcript,
        language: str,
        tenant_id: str,
        guardian_id: str,
        student_id: str,
        started: float,
        queued: float,
        transcribed: float,
        upload_ms: int,
        decode_ms: int,
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
            "queue": round((queued - started) * 1000),
            "stt": round((transcribed - queued) * 1000),
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


_RETRY_REPLIES = {
    "en": "I could not hear a clear question. Please try again.",
    "hi": "मुझे सवाल साफ़ सुनाई नहीं दिया। कृपया फिर से बोलें।",
    "te": "మీ ప్రశ్న స్పష్టంగా వినిపించలేదు. దయచేసి మళ్లీ చెప్పండి.",
}


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


def audio_owner_key(tenant_id: str, guardian_id: str) -> str:
    return f"{tenant_id}\0{guardian_id}"


def _is_unusable_transcript(transcript: Transcript) -> bool:
    words = re.findall(r"\w+", transcript.text.casefold())
    if not words or transcript.confidence < 0.15:
        return True
    if len(words) >= 4 and len(set(words)) <= max(1, len(words) // 4):
        return True
    return False
