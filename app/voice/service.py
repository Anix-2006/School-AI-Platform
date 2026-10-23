from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import defaultdict, deque

from app.config import settings
from app.services.chat_service import run_parent_turn
from app.voice.constants import COULD_NOT_HEAR
from app.voice.speech_text import clean_for_speech
from app.voice.stt import SpeechToText, Transcript
from app.voice.storage import AudioStore
from app.voice.tts import TextToSpeech
from app.voice.tts_policy import (
    TtsCircuit,
    non_negative_float,
    positive_int,
    post_if_active,
    run_blocking,
    timeout_seconds,
)

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
        stt: SpeechToText,
        tts: TextToSpeech,
        audio_store: AudioStore,
        *,
        max_concurrency: int = 1,
        queue_timeout: float = 2.0,
        guardian_rate: int = 10,
        global_rate: int = 60,
        speak_rate: int = 20,
        max_seconds: float = 30.0,
        tts_timeout: float | None = None,
        tts_retries: int | None = None,
        tts_retry_delay: float | None = None,
        tts_cooldown: float | None = None,
        tts_cooldown_failures: int | None = None,
    ):
        self.stt = stt
        self.tts = tts
        self.audio_store = audio_store
        self.queue_timeout = queue_timeout
        self.guardian_rate = guardian_rate
        self.global_rate = global_rate
        self.speak_rate = speak_rate
        self.max_seconds = max_seconds
        self.tts_timeout = timeout_seconds(
            settings.tts_timeout_seconds if tts_timeout is None else tts_timeout
        )
        self.tts_retries = positive_int(
            settings.tts_retries if tts_retries is None else tts_retries, 1
        )
        self.tts_retry_delay = non_negative_float(
            settings.tts_retry_delay_seconds if tts_retry_delay is None else tts_retry_delay,
            0.2,
        )
        self._tts_circuit = TtsCircuit(
            failures_before_open=max(
                1,
                positive_int(
                    settings.tts_cooldown_failures
                    if tts_cooldown_failures is None
                    else tts_cooldown_failures,
                    3,
                ),
            ),
            cooldown_seconds=non_negative_float(
                settings.tts_cooldown_seconds if tts_cooldown is None else tts_cooldown,
                30.0,
            ),
        )
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
        lock_language: bool = False,
        on_sentence=None,
    ) -> dict:
        self.check_rate("chat", guardian["id"])
        started = time.perf_counter()
        fallback_language = _voice_language(language, guardian)
        if lock_language:
            fallback_language = _speakable_language(fallback_language, self.tts)
        elif fallback_language not in {"en", "hi"}:
            # Transcript detection still chooses the reply for /voice/chat.
            # Its empty-audio fallback stays English or Hindi.
            fallback_language = "en"
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout)
        except TimeoutError:
            raise VoiceError(429, "Voice service is busy; please try again") from None
        queued = time.perf_counter()
        try:
            prompt = _initial_prompt(student)
            stt_language = _stt_language(language, guardian)
            transcript = await asyncio.to_thread(
                self._transcribe, audio, stt_language, prompt
            )
            transcribed = time.perf_counter()
            # The browser demo speaks in the guardian's language. Other callers
            # still follow the transcript.
            reply_language = (
                fallback_language if lock_language else choose_reply_language(transcript)
            )
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
                    started,
                    queued,
                    transcribed,
                    upload_ms,
                    decode_ms,
                    on_sentence=on_sentence,
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
                reply_language,
            )
            replied = time.perf_counter()
            spoken_text = clean_for_speech(result["reply"], reply_language)
            speech, tts_failed = await self._speak(
                spoken_text, reply_language, on_sentence=on_sentence
            )
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
            _log_voice_turn(
                guardian["id"], session_id, reply_language, timings_ms, tts_failed
            )
            return {
                **result,
                "language": reply_language,
                "transcript": transcript.text,
                "spoken_text": spoken_text,
                "audio_id": audio_id,
                "tts_degraded": not bool(speech),
                "error_stage": "tts" if tts_failed else None,
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
        on_sentence=None,
    ) -> dict:
        reply = _RETRY_REPLIES.get(language, _RETRY_REPLIES["en"])
        tts_started = time.perf_counter()
        speech, tts_failed = await self._speak(reply, language, on_sentence=on_sentence)
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
        _log_voice_turn(guardian_id, None, language, timings_ms, tts_failed)
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
            "error_stage": "tts" if tts_failed else None,
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
            speech, failed = await self._speak(cleaned, language, degrade=False)
        except ValueError as exc:
            raise VoiceError(422, str(exc)) from None
        except Exception:
            raise VoiceError(503, "Text-to-speech is temporarily unavailable") from None
        if failed or not speech:
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
        self, text: str, language: str, *, degrade: bool = True, on_sentence=None
    ) -> tuple[bytes, bool]:
        """Return ``(audio, failed)``. A failure is no audio, not a lost reply.

        Each attempt stops at ``tts_timeout``. One retry follows a brief wait.
        After enough failures, the cool-down skips the engine entirely.
        """
        if self._tts_circuit.cooling_down():
            if not degrade:
                raise RuntimeError("Speech generation is in cool-down")
            return b"", True
        attempts = self.tts_retries + 1
        emitted = False

        async def watch(index: int, sentence: str, audio: bytes) -> None:
            nonlocal emitted
            emitted = True
            await on_sentence(index, sentence, audio)

        last_error: Exception | None = None
        for attempt in range(attempts):
            if self._tts_circuit.cooling_down():
                if not degrade:
                    raise RuntimeError("Speech generation is in cool-down")
                return b"", True
            try:
                if on_sentence is not None and hasattr(self.tts, "speak_streaming"):
                    audio = await asyncio.wait_for(
                        self._stream_speech(text, language, watch),
                        timeout=self.tts_timeout,
                    )
                else:
                    audio = await run_blocking(
                        lambda: self.tts.speak(text, language),
                        self.tts_timeout,
                    )
            except ValueError:
                raise
            except Exception as exc:
                last_error = exc
                self._tts_circuit.record_failure()
                # A sentence already sent to the browser must not be generated again.
                if emitted or attempt + 1 >= attempts:
                    break
                if self.tts_retry_delay:
                    await asyncio.sleep(self.tts_retry_delay)
                continue
            self._tts_circuit.record_success()
            return audio or b"", False
        if not degrade:
            raise last_error or RuntimeError("Speech generation failed")
        return b"", True

    async def _stream_speech(self, text: str, language: str, on_sentence) -> bytes:
        """Run sentence synthesis off the event loop and emit each sentence in order."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        active = threading.Event()
        active.set()

        def emit(index: int, sentence: str, audio: bytes) -> None:
            post_if_active(loop, queue, ("sentence", index, sentence, audio), active)

        def work() -> None:
            try:
                audio = self.tts.speak_streaming(
                    text, language, on_sentence=emit, join=False
                )
                outcome = ("done", audio)
            except Exception as exc:
                outcome = ("error", exc)
            post_if_active(loop, queue, outcome, active)

        threading.Thread(target=work, name="tts-stream", daemon=True).start()
        try:
            while True:
                item = await queue.get()
                kind = item[0]
                if kind == "sentence":
                    _, index, sentence, audio = item
                    await on_sentence(index, sentence, audio)
                elif kind == "done":
                    return b""
                else:
                    raise item[1]
        finally:
            active.clear()

    def close(self) -> None:
        close = getattr(self.tts, "close", None)
        if close is not None:
            close()


_RETRY_REPLIES = COULD_NOT_HEAR


def _initial_prompt(student: dict) -> str:
    name = str(student.get("name") or "").strip()
    subject = f"Student {name}." if name else "The linked student."
    return (
        f"{subject} School terms: attendance, homework, syllabus, "
        "FA1, FA2, formative assessment, summative assessment."
    )


def _voice_language(override: str | None, guardian: dict) -> str:
    chosen = override or guardian.get("preferred_language") or "en"
    code = str(chosen).strip().lower().split("-", 1)[0]
    return code or "en"


def _speakable_language(language: str, tts: object) -> str:
    """Return a language this engine will actually speak.

    English, Hindi, and Telugu are used when the engine lists them.
    Any other language is spoken in English. Callers report this spoken
    language, not the guardian's original choice.
    """
    supported = getattr(tts, "SUPPORTED_LANGUAGES", None) or {"en", "hi", "te"}
    if language in supported:
        return language
    if "en" in supported:
        return "en"
    return next(iter(supported))


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


def _log_voice_turn(
    guardian_id: str,
    session_id: str | None,
    language: str,
    timings_ms: dict,
    tts_failed: bool,
) -> None:
    if tts_failed:
        logger.info(
            "voice_turn guardian_id=%s session_id=%s language=%s timings_ms=%s error_stage=tts",
            guardian_id,
            session_id or "default",
            language,
            timings_ms,
        )
        return
    logger.info(
        "voice_turn guardian_id=%s session_id=%s language=%s timings_ms=%s",
        guardian_id,
        session_id or "default",
        language,
        timings_ms,
    )


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
