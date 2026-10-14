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


SUPPORTED_LANGUAGES = ("en", "hi")
# Whisper often labels Hindi speech as Urdu (same spoken language, different script).
_HINDI_ALIASES = ("hi", "ur")
# Retry repetitive/low-confidence decodes (e.g. "ॐ ॐ ॐ" on quiet audio) at a few
# temperatures; kept short to bound CPU latency.
_FALLBACK_TEMPERATURES = (0.0, 0.2, 0.4)


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

    def __init__(
        self,
        model_name: str,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | None = None,
        local_files_only: bool = False,
    ):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self.local_files_only = local_files_only
        self._model = None
        self._load_lock = threading.Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                try:
                    self._model = WhisperModel(
                        self.model_name,
                        device=self.device,
                        compute_type=self.compute_type,
                        download_root=self.download_root,
                        local_files_only=self.local_files_only,
                    )
                except FileNotFoundError as exc:
                    if not self.local_files_only:
                        raise
                    raise RuntimeError(
                        f"Whisper model '{self.model_name}' is not downloaded in "
                        f"{self.download_root}. Run: python -m app.voice.download_model"
                    ) from exc

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
        segments, info = self._decode(audio_bytes, language, initial_prompt)
        if language is None and info.language not in SUPPORTED_LANGUAGES:
            # Segments are lazy, so only language detection has run so far.
            segments, info = self._decode(
                audio_bytes, closest_supported_language(info), initial_prompt
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

    def _decode(self, audio_bytes: bytes, language: str | None, initial_prompt: str | None):
        return self._model.transcribe(
            io.BytesIO(audio_bytes),
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=True,
            beam_size=1,
            best_of=1,
            temperature=_FALLBACK_TEMPERATURES,
            condition_on_previous_text=False,
        )


def closest_supported_language(info) -> str:
    """Pick English or Hindi from Whisper's language probabilities."""
    probs = dict(getattr(info, "all_language_probs", None) or [])
    if not probs:
        return "hi" if getattr(info, "language", None) in _HINDI_ALIASES else "en"
    hindi = sum(probs.get(code, 0.0) for code in _HINDI_ALIASES)
    return "hi" if hindi >= probs.get("en", 0.0) else "en"
