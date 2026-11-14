from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

from app.config import settings
from app.services.chat_service import run_parent_turn
from app.voice.constants import COULD_NOT_HEAR
from app.voice.speech_text import clean_for_speech
from app.voice.stt import SpeechToText, Transcript
from app.voice.storage import AudioStore
from app.voice.transcription_queue import (
    TranscriptionQueue,
    TranscriptionQueueClosed,
    TranscriptionQueueFull,
)
from app.voice.tts import TextToSpeech

operations_logger = logging.getLogger("uvicorn.error")

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
        stt: SpeechToText,
        tts: TextToSpeech,
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
        tts_timeout: float | None = None,
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
        self.tts_timeout = (
            settings.voice_tts_timeout if tts_timeout is None else tts_timeout
        )
        self._queue = TranscriptionQueue(
            max_parallel=self.max_parallel,
            queue_max=self.queue_max,
            notify_at=self.queue_notify_at,
        )
        # Browser text turns skip STT; keep a small concurrency gate for agent work.
        self._slots = asyncio.Semaphore(self.max_parallel)
        self._rates = SlidingRateLimiter()

    def check_rate(self, action: str, guardian_id: str) -> None:
        limit = self.guardian_rate if action == "chat" else self.speak_rate
        if not self._rates.allow(action, guardian_id, limit):
            operations_logger.warning(
                "voice_rejected reason=guardian_rate action=%s guardian_id=%s",
                action,
                guardian_id,
            )
            raise VoiceError(429, "Too many voice requests. Please wait up to one minute.")
        if not self._rates.allow("global", "__global__", self.global_rate):
            operations_logger.warning("voice_rejected reason=global_rate action=%s", action)
            raise VoiceError(429, "The voice service request limit was reached. Please wait one minute.")

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
        del content_type  # callers pass MIME; STT expects WAV bytes after decode
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
            stt_language = _stt_language(language, guardian)
            stt_started = time.perf_counter()
            try:
                return await asyncio.to_thread(
                    self._transcribe, audio, stt_language, prompt
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
            transcript.duration_seconds is not None
            and transcript.duration_seconds > self.max_seconds
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
                queue_notice,
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
        speech = await self._speak(spoken_text, reply_language)
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
        operations_logger.info(
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

    async def text_chat(
        self,
        *,
        tenant_id: str,
        guardian: dict,
        student: dict,
        transcript: str,
        language: str | None,
        session_id: str | None,
    ) -> dict:
        """Run a browser-transcribed voice turn without server STT or TTS."""
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout)
        except TimeoutError:
            operations_logger.warning(
                "voice_rejected reason=queue_busy guardian_id=%s timeout_seconds=%.2f",
                guardian["id"],
                self.queue_timeout,
            )
            raise VoiceError(
                429, "Voice service is processing another request. Please try again."
            ) from None
        queued = time.perf_counter()
        try:
            self.check_rate("chat", guardian["id"])
            reply_language = choose_reply_language(
                Transcript(
                    text=transcript,
                    language=language,
                    duration_seconds=None,
                )
            )
            thread_id = (
                f"{guardian['id']}:{session_id}" if session_id else guardian["id"]
            )
            result = await asyncio.to_thread(
                run_parent_turn,
                tenant_id,
                guardian,
                student,
                transcript,
                "voice",
                thread_id,
                reply_language,
            )
            finished = time.perf_counter()
            router_ms = int(result.get("router_ms") or 0)
            agent_ms = int(
                result.get("agent_llm_ms")
                or max(0, round((finished - queued) * 1000) - router_ms)
            )
            timings_ms = {
                "upload": 0,
                "decode": 0,
                "queue": round((queued - started) * 1000),
                "stt": 0,
                "router": router_ms,
                "agent_llm": agent_ms,
                "tts": 0,
            }
            timings_ms["total"] = sum(timings_ms.values())
            operations_logger.info(
                "voice_text_turn guardian_id=%s session_id=%s language=%s timings_ms=%s",
                guardian["id"],
                session_id or "default",
                reply_language,
                timings_ms,
            )
            return {
                **result,
                "language": reply_language,
                "transcript": transcript,
                "audio_id": None,
                "timings": {
                    "queue": timings_ms["queue"],
                    "stt": 0,
                    "llm": timings_ms["router"] + timings_ms["agent_llm"],
                    "tts": 0,
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
        upload_ms: int,
        decode_ms: int,
        wait_ms: int,
        stt_ms: int,
        queue_notice: str | None = None,
    ) -> dict:
        reply = _RETRY_REPLIES.get(language, _RETRY_REPLIES["en"])
        tts_started = time.perf_counter()
        speech = await self._speak(reply, language)
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
            "queue_notice": queue_notice,
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
            speech = await self._speak(cleaned, language, degrade=False)
        except ValueError as exc:
            raise VoiceError(422, str(exc)) from None
        except Exception:
            raise VoiceError(503, "Text-to-speech is temporarily unavailable") from None
        if not speech:
            raise VoiceError(503, "Text-to-speech is temporarily unavailable")
        return speech

    def _transcribe(
        self, audio: bytes, language: str | None, initial_prompt: str | None
    ) -> Transcript:
        # WhisperSTT accepts initial_prompt; plain Protocol fakes may ignore it.
        try:
            return self.stt.transcribe(  # type: ignore[call-arg]
                audio, language, initial_prompt=initial_prompt
            )
        except TypeError:
            return self.stt.transcribe(audio, language)

    async def _speak(
        self, text: str, language: str, *, degrade: bool = True
    ) -> bytes:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.tts.speak, text, language),
                timeout=self.tts_timeout,
            )
        except ValueError:
            raise
        except Exception:
            if degrade:
                return b""
            raise

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


_RETRY_REPLIES = COULD_NOT_HEAR

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
    if override in {"en", "hi"}:
        return override
    preferred = guardian.get("preferred_language")
    return preferred if preferred in {"en", "hi"} else "en"


def _stt_language(override: str | None, guardian: dict) -> str | None:
    """Pass a language to Whisper when known so it skips detection."""
    if override in {"en", "hi", "te"}:
        return override
    preferred = guardian.get("preferred_language")
    return preferred if preferred in {"en", "hi", "te"} else None


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
