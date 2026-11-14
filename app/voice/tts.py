from __future__ import annotations

import hashlib
import io
import re
import threading
from collections import OrderedDict
from typing import Protocol

from app.config import settings

# Our language codes -> gTTS lang codes.
_GTTS_LANG = {
    "en": "en",
    "hi": "hi",
    "te": "te",
}


class TextToSpeech(Protocol):
    def speak(self, text: str, language: str) -> bytes: ...


class GTTSSpeaker:
    """gTTS implementation behind the TextToSpeech interface."""

    SUPPORTED_LANGUAGES = frozenset(_GTTS_LANG)

    def __init__(self, *, cache_size: int | None = None):
        self.cache_size = max(1, cache_size if cache_size is not None else settings.tts_cache_size)
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_lock = threading.Lock()

    def speak(self, text: str, language: str) -> bytes:
        gtts_lang = _GTTS_LANG.get(language)
        if gtts_lang is None:
            raise ValueError(
                f"Unsupported voice language '{language}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_LANGUAGES))}."
            )
        key = hashlib.sha256(f"{language}\0{text}".encode()).hexdigest()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        parts = [self._render(chunk, gtts_lang) for chunk in _sentence_chunks(text)]
        audio = b"".join(parts)
        with self._cache_lock:
            self._cache[key] = audio
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return audio

    def close(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    @staticmethod
    def _render(text: str, gtts_lang: str) -> bytes:
        from gtts import gTTS

        target = io.BytesIO()
        kwargs: dict = {"text": text, "lang": gtts_lang}
        if gtts_lang == "en":
            kwargs["tld"] = "co.in"
        gTTS(**kwargs).write_to_fp(target)
        return target.getvalue()


def _sentence_chunks(text: str, limit: int = 180) -> list[str]:
    sentences = re.split(r"(?<=[.!?।])\s+", text.strip())
    chunks: list[str] = []
    for sentence in sentences:
        while len(sentence) > limit:
            split_at = sentence.rfind(" ", 0, limit + 1)
            split_at = split_at if split_at > 0 else limit
            chunks.append(sentence[:split_at].strip())
            sentence = sentence[split_at:].strip()
        if sentence:
            chunks.append(sentence)
    return chunks or [text.strip()]


_tts: GTTSSpeaker | None = None
_tts_lock = threading.Lock()


def get_tts() -> GTTSSpeaker:
    """The single shared text-to-speech engine (same pattern as get_store())."""
    global _tts
    if _tts is None:
        with _tts_lock:
            if _tts is None:
                _tts = GTTSSpeaker()
    return _tts


def reset_tts_for_tests() -> None:
    global _tts
    with _tts_lock:
        if _tts is not None:
            _tts.close()
        _tts = None
