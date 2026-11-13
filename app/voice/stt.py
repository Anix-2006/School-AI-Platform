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


SUPPORTED_LANGUAGES = ("en", "hi")
# Whisper often labels Hindi speech as Urdu (same spoken language, different script).
_HINDI_ALIASES = ("hi", "ur")
# Retry repetitive/low-confidence decodes (e.g. "ॐ ॐ ॐ" on quiet audio) at a few
# temperatures; kept short to bound CPU latency.
_FALLBACK_TEMPERATURES = (0.0, 0.2, 0.4)


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
        cpu_threads: int | None = None,
        num_workers: int | None = None,
        download_root: str | None = None,
        local_files_only: bool | None = None,
    ):
        self.model_name = model_name or settings.whisper_model
        self.device = device or settings.whisper_device
        self.compute_type = compute_type or settings.whisper_compute_type
        self.cpu_threads = max(
            1,
            settings.whisper_cpu_threads if cpu_threads is None else cpu_threads,
        )
        self.num_workers = max(
            1,
            settings.whisper_num_workers if num_workers is None else num_workers,
        )
        self.download_root = download_root
        self.local_files_only = bool(local_files_only)
        self._model = None
        self._load_lock = threading.Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                kwargs: dict = {
                    "device": self.device,
                    "compute_type": self.compute_type,
                    "cpu_threads": self.cpu_threads,
                    "num_workers": self.num_workers,
                }
                if self.download_root is not None:
                    kwargs["download_root"] = self.download_root
                if self.local_files_only:
                    kwargs["local_files_only"] = True
                try:
                    self._model = WhisperModel(self.model_name, **kwargs)
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
        audio_wav: bytes,
        language: str | None = None,
        *,
        initial_prompt: str | None = None,
    ) -> Transcript:
        self.load()
        segments, info = self._decode(audio_wav, language, initial_prompt)
        if language is None and getattr(info, "language", None) not in SUPPORTED_LANGUAGES:
            # Segments are lazy, so only language detection has run so far.
            segments, info = self._decode(
                audio_wav, closest_supported_language(info), initial_prompt
            )
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

    def _decode(
        self, audio_wav: bytes, language: str | None, initial_prompt: str | None
    ):
        kwargs: dict = {
            "vad_filter": True,
            "beam_size": 1,
            "best_of": 1,
            "temperature": _FALLBACK_TEMPERATURES,
            "condition_on_previous_text": False,
        }
        if language:
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        return self._model.transcribe(io.BytesIO(audio_wav), **kwargs)


def closest_supported_language(info) -> str:
    """Pick English or Hindi from Whisper's language probabilities."""
    probs = dict(getattr(info, "all_language_probs", None) or [])
    if not probs:
        return "hi" if getattr(info, "language", None) in _HINDI_ALIASES else "en"
    hindi = sum(probs.get(code, 0.0) for code in _HINDI_ALIASES)
    return "hi" if hindi >= probs.get("en", 0.0) else "en"


_stt: WhisperSTT | None = None
_stt_lock = threading.Lock()


def get_stt() -> WhisperSTT:
    """The single shared speech-to-text engine (same pattern as get_store())."""
    global _stt
    if _stt is None:
        with _stt_lock:
            if _stt is None:
                from app.services.excel_store import resolve_path

                # Startup / requests only load a model already fetched by
                # `python -m app.voice.download_model`; they never download.
                _stt = WhisperSTT(
                    download_root=str(resolve_path(settings.whisper_model_dir)),
                    local_files_only=True,
                )
    return _stt


def reset_stt_for_tests() -> None:
    global _stt
    with _stt_lock:
        _stt = None
