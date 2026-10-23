"""One shared voice turn: audio in → STT → agents → speech cleanup → TTS → audio out.

Channels (browser, WhatsApp) verify the guardian/student first, then call
``run_voice_turn``. This module does not check identity or consent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.services.chat_service import run_parent_turn
from app.voice.constants import COULD_NOT_HEAR, DEFAULT_LANGUAGE, phrase
from app.voice.speech_text import to_speech_text
from app.voice.stt import SpeechToText, get_stt
from app.voice.tts import TextToSpeech, get_tts
from app.voice.tts_policy import (
    circuit_for,
    non_negative_float,
    positive_int,
    speak_bounded,
    timeout_seconds,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceReply:
    transcript: str
    reply_text: str
    speech_text: str
    audio: bytes
    agent_used: str | None
    language: str


def run_voice_turn(
    tenant_id: str,
    guardian: dict,
    student: dict,
    audio_wav: bytes,
    channel: str,
    thread_id: str,
    *,
    stt: SpeechToText | None = None,
    tts: TextToSpeech | None = None,
) -> VoiceReply:
    """Run one voice turn through the same orchestrator as text chat.

    ``guardian`` and ``student`` must already be verified by the caller.
    Language comes from the guardian's ``preferred_language``.
    """
    language = _preferred_language(guardian)
    engine = stt or get_stt()
    speaker = tts or get_tts()

    transcript = engine.transcribe(audio_wav, language)
    heard = (transcript.text or "").strip()

    if not heard:
        reply_text = phrase("could_not_hear", language)
        speech_text = to_speech_text(reply_text, language)
        audio, failed = _speak_reply(speaker, speech_text, language)
        logger.info(
            "voice_pipeline empty_transcript guardian_id=%s language=%s channel=%s%s",
            guardian.get("id"),
            language,
            channel,
            " error_stage=tts" if failed else "",
        )
        return VoiceReply(
            transcript="",
            reply_text=reply_text,
            speech_text=speech_text,
            audio=audio,
            agent_used=None,
            language=language,
        )

    result = run_parent_turn(
        tenant_id,
        guardian,
        student,
        heard,
        channel,
        thread_id,
        language,
    )
    reply_text = result["reply"]
    speech_text = to_speech_text(reply_text, language)
    audio, failed = _speak_reply(speaker, speech_text, language)
    if failed:
        logger.info(
            "voice_pipeline tts_failed guardian_id=%s language=%s channel=%s error_stage=tts",
            guardian.get("id"),
            language,
            channel,
        )
    return VoiceReply(
        transcript=heard,
        reply_text=reply_text,
        speech_text=speech_text,
        audio=audio,
        agent_used=result.get("agent_used"),
        language=language,
    )


def _speak_reply(speaker: TextToSpeech, text: str, language: str) -> tuple[bytes, bool]:
    """Return speech, or no audio when synthesis fails. The text reply still stands."""
    return speak_bounded(
        speaker,
        text,
        language,
        circuit_for(speaker),
        timeout=timeout_seconds(settings.tts_timeout_seconds),
        retries=positive_int(settings.tts_retries, 1),
        retry_delay=non_negative_float(settings.tts_retry_delay_seconds, 0.2),
    )


def _preferred_language(guardian: dict) -> str:
    preferred = (guardian.get("preferred_language") or DEFAULT_LANGUAGE).strip().lower()
    if preferred in COULD_NOT_HEAR:
        return preferred
    return DEFAULT_LANGUAGE
