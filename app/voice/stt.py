from __future__ import annotations

import io
import threading
import wave
from dataclasses import dataclass
from typing import Protocol

from app.config import settings


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    duration_seconds: float | None
    # Used by the voice service to reject silence / low-quality guesses.
    confidence: float = 1.0


class SpeechToText(Protocol):
    def load(self) -> None: ...

    def transcribe(
        self,
        audio_wav: bytes,
        language: str | None = None,
    ) -> Transcript: ...


class WhisperSTT:
    """faster-whisper implementation. One model instance per process."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ):
        self.model_name = model_name or settings.whisper_model
        self.device = device or settings.whisper_device
        self.compute_type = compute_type or settings.whisper_compute_type
        self._model = None
        self._load_lock = threading.Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                )

    def warmup(self) -> None:
        """Run one silent second at startup so the first parent does not pay setup cost."""
        self.load()
        target = io.BytesIO()
        with wave.open(target, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * 16000)
        segments, _info = self._model.transcribe(
            io.BytesIO(target.getvalue()),
            language="en",
            vad_filter=True,
            beam_size=1,
            best_of=1,
            temperature=0,
            condition_on_previous_text=False,
        )
        list(segments)

    def transcribe(
        self,
        audio_wav: bytes,
        language: str | None = None,
        *,
        initial_prompt: str | None = None,
    ) -> Transcript:
        self.load()
        # Pass language when known so Whisper skips detection; detect only when missing.
        kwargs: dict = {
            "vad_filter": True,
            "beam_size": 1,
            "best_of": 1,
            "temperature": 0,
            "condition_on_previous_text": False,
        }
        if language:
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        segments, info = self._model.transcribe(io.BytesIO(audio_wav), **kwargs)
        parts = list(segments)
        text = " ".join(
            segment.text.strip() for segment in parts if segment.text.strip()
        ).strip()
        confidence = 1.0 - (
            sum(float(getattr(segment, "no_speech_prob", 0.0)) for segment in parts)
            / len(parts)
            if parts
            else 1.0
        )
        detected = getattr(info, "language", None)
        return Transcript(
            text=text,
            language=detected or language,
            duration_seconds=getattr(info, "duration", None),
            confidence=max(0.0, min(1.0, confidence)),
        )


_stt: WhisperSTT | None = None
_stt_lock = threading.Lock()


def get_stt() -> WhisperSTT:
    """The single shared speech-to-text engine (same pattern as get_store())."""
    global _stt
    if _stt is None:
        with _stt_lock:
            if _stt is None:
                _stt = WhisperSTT()
    return _stt


def reset_stt_for_tests() -> None:
    global _stt
    with _stt_lock:
        _stt = None
