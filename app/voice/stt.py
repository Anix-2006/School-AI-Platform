from __future__ import annotations

import io
import threading
import wave
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    confidence: float
    duration: float | None


class SttEngine(Protocol):
    def load(self) -> None: ...
    def warmup(self) -> None: ...

    def transcribe(
        self,
        audio_bytes: bytes,
        content_type: str,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> Transcript: ...


class FasterWhisperEngine:
    """Lazy import with one model instance per runtime."""

    def __init__(self, model_name: str, device: str = "cpu", compute_type: str = "int8"):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._load_lock = threading.Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=self.compute_type
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
        audio_bytes: bytes,
        content_type: str,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> Transcript:
        self.load()
        segments, info = self._model.transcribe(
            io.BytesIO(audio_bytes),
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=True,
            beam_size=1,
            best_of=1,
            temperature=0,
            condition_on_previous_text=False,
        )
        parts = list(segments)
        text = " ".join(segment.text.strip() for segment in parts if segment.text.strip()).strip()
        confidence = 1.0 - (
            sum(float(getattr(segment, "no_speech_prob", 0.0)) for segment in parts)
            / len(parts)
            if parts
            else 1.0
        )
        return Transcript(
            text=text,
            language=getattr(info, "language", language),
            confidence=max(0.0, min(1.0, confidence)),
            duration=getattr(info, "duration", None),
        )
