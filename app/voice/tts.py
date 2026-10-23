from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Protocol

from app.config import settings

# Our language codes -> gTTS lang codes.
_GTTS_LANG = {
    "en": "en",
    "hi": "hi",
    "te": "te",
}

# gTTS rejects very long single requests. A sentence longer than this is
# spoken in pieces, then joined, before that sentence is delivered.
_GTTS_REQUEST_CHARS = 180

_DOT = "\u0001"
_ABBREVIATION = re.compile(
    r"(?i)\b(?:e\.g|i\.e|a\.m|p\.m|mrs|mr|ms|dr|prof|std|sr|jr|st|vs|etc|fig|rs)\."
)
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_ELLIPSIS = re.compile(r"\.{2,}")
_SENTENCE_END = re.compile(r"([.?!।]+)")

SentenceReady = Callable[[int, str, bytes], None]


class TextToSpeech(Protocol):
    def speak(self, text: str, language: str) -> bytes: ...


class GTTSSpeaker:
    """gTTS implementation behind the TextToSpeech interface."""

    SUPPORTED_LANGUAGES = frozenset(_GTTS_LANG)

    def __init__(self, *, cache_size: int | None = None):
        self.cache_size = max(1, cache_size if cache_size is not None else settings.tts_cache_size)
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}

    def speak(self, text: str, language: str) -> bytes:
        return self.speak_streaming(text, language, on_sentence=None, join=True)

    def speak_streaming(
        self,
        text: str,
        language: str,
        *,
        on_sentence: SentenceReady | None = None,
        join: bool = True,
    ) -> bytes:
        """Speak each sentence concurrently and deliver them in the original order.

        Later sentences may finish first. ``on_sentence`` is still called from
        sentence 0 onward, so live playback can start on the first sentence
        while the rest are generated. Voice notes pass ``join=True`` and receive
        one MP3. Each sentence is cached on its own.
        """
        gtts_lang = _GTTS_LANG.get(language)
        if gtts_lang is None:
            raise ValueError(
                f"Unsupported voice language '{language}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_LANGUAGES))}."
            )
        sentences = split_sentences(text, language)
        if not sentences:
            return b""
        pieces = [_gtts_pieces(sentence) for sentence in sentences]
        workers = _parallel_workers()
        audios: list[bytes] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            grouped = [
                [pool.submit(self._cached_render, chunk, gtts_lang) for chunk in chunks]
                for chunks in pieces
            ]
            for index, futures in enumerate(grouped):
                parts = [future.result() for future in futures]
                audio = parts[0] if len(parts) == 1 else concat_mp3(parts)
                audios.append(audio)
                if on_sentence is not None:
                    on_sentence(index, sentences[index], audio)
        if not join:
            return b""
        return audios[0] if len(audios) == 1 else concat_mp3(audios)

    def close(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            pending = list(self._inflight.values())
            self._inflight.clear()
        for event in pending:
            event.set()

    def _cached_render(self, text: str, gtts_lang: str) -> bytes:
        tld = "co.in" if gtts_lang == "en" else ""
        key = hashlib.sha256(f"{gtts_lang}\0{tld}\0{text}".encode()).hexdigest()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            wait_for = self._inflight.get(key)
            if wait_for is None:
                wait_for = threading.Event()
                self._inflight[key] = wait_for
                owner = True
            else:
                owner = False
        if not owner:
            wait_for.wait()
            with self._cache_lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    return cached
            return self._cached_render(text, gtts_lang)
        try:
            audio = self._render(text, gtts_lang)
            if not audio:
                raise RuntimeError("Speech synthesis returned no audio")
            with self._cache_lock:
                self._cache[key] = audio
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            return audio
        finally:
            with self._cache_lock:
                self._inflight.pop(key, None)
            wait_for.set()

    @staticmethod
    def _render(text: str, gtts_lang: str) -> bytes:
        from gtts import gTTS

        target = io.BytesIO()
        kwargs: dict = {"text": text, "lang": gtts_lang}
        if gtts_lang == "en":
            kwargs["tld"] = "co.in"
        gTTS(**kwargs).write_to_fp(target)
        return target.getvalue()


def split_sentences(text: str, language: str = "en") -> list[str]:
    """Split speech into sentences without breaking abbreviations or decimals.

    ``language`` selects the same end punctuation for English, Hindi, and
    Telugu: ``.``, ``?``, ``!``, and the danda ``।``. Pieces shorter than
    ``tts_min_sentence_chars`` are merged into the following sentence.
    """
    if not isinstance(language, str):
        raise TypeError("language must be a string")
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    protected = _ELLIPSIS.sub("…", cleaned)
    protected = _DECIMAL.sub(_DOT, protected)
    protected = _ABBREVIATION.sub(lambda match: match.group(0).replace(".", _DOT), protected)
    parts = _SENTENCE_END.split(protected)
    sentences: list[str] = []
    pending = ""
    for part in parts:
        if _SENTENCE_END.fullmatch(part):
            pending += part
            sentence = pending.replace(_DOT, ".").strip()
            if sentence:
                sentences.append(sentence)
            pending = ""
        else:
            pending += part
    tail = pending.replace(_DOT, ".").strip()
    if tail:
        sentences.append(tail)
    return _merge_short(sentences, max(1, int(settings.tts_min_sentence_chars)))


def concat_mp3(parts: list[bytes]) -> bytes:
    """Join MP3 clips with ffmpeg so the result plays as one file, in order."""
    usable = [part for part in parts if part]
    if not usable:
        return b""
    if len(usable) == 1:
        return usable[0]
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to join speech")
    with tempfile.TemporaryDirectory(prefix="tts-join-") as directory:
        root = Path(directory)
        lines: list[str] = []
        for index, part in enumerate(usable):
            path = root / f"part{index}.mp3"
            path.write_bytes(part)
            lines.append(f"file '{path.as_posix()}'")
        list_path = root / "list.txt"
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        target = root / "joined.mp3"
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(target),
        ]
        if not _run_ffmpeg(command) or not target.is_file() or target.stat().st_size == 0:
            command = [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(target),
            ]
            if not _run_ffmpeg(command) or not target.is_file():
                raise RuntimeError("Could not join speech audio")
        return target.read_bytes()


def _parallel_workers() -> int:
    """How many gTTS calls may run at once. A bad value cannot mean unlimited."""
    try:
        requested = int(settings.tts_max_parallel)
    except (TypeError, ValueError):
        return 3
    if requested < 1:
        return 1
    return requested


def _merge_short(sentences: list[str], minimum: int) -> list[str]:
    merged: list[str] = []
    pending = ""
    for sentence in sentences:
        pending = f"{pending} {sentence}".strip() if pending else sentence
        if len(pending) >= minimum:
            merged.append(pending)
            pending = ""
    if pending:
        if merged:
            merged[-1] = f"{merged[-1]} {pending}".strip()
        else:
            merged.append(pending)
    return merged


def _gtts_pieces(sentence: str) -> list[str]:
    if len(sentence) <= _GTTS_REQUEST_CHARS:
        return [sentence]
    pieces: list[str] = []
    remaining = sentence
    while len(remaining) > _GTTS_REQUEST_CHARS:
        split_at = remaining.rfind(" ", 0, _GTTS_REQUEST_CHARS + 1)
        split_at = split_at if split_at > 0 else _GTTS_REQUEST_CHARS
        pieces.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces or [sentence]


def _run_ffmpeg(command: list[str]) -> bool:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=30,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


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
