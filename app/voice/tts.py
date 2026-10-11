from __future__ import annotations

import asyncio
import hashlib
import io
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol


class TtsEngine(Protocol):
    async def synthesize(self, text: str, language: str) -> bytes: ...


class GttsEngine:
    SUPPORTED_LANGUAGES = {"en", "hi", "te"}

    def __init__(self, *, timeout: float = 12.0, cache_size: int = 128, workers: int = 4):
        self.timeout = timeout
        self.cache_size = max(1, cache_size)
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="gtts")

    async def synthesize(self, text: str, language: str) -> bytes:
        if language not in self.SUPPORTED_LANGUAGES:
            raise ValueError("Unsupported voice language")
        key = hashlib.sha256(f"{language}\0{text}".encode()).hexdigest()
        async with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        parts = await asyncio.gather(
            *(self._render_with_retry(chunk, language) for chunk in _sentence_chunks(text))
        )
        audio = b"".join(parts)
        async with self._cache_lock:
            self._cache[key] = audio
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return audio

    async def _render_with_retry(self, text: str, language: str) -> bytes:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                loop = asyncio.get_running_loop()
                task = loop.run_in_executor(self._executor, self._render, text, language)
                return await asyncio.wait_for(task, timeout=self.timeout)
            except Exception as exc:
                last_error = exc
        raise RuntimeError("Text-to-speech is temporarily unavailable") from last_error

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _render(text: str, language: str) -> bytes:
        from gtts import gTTS

        target = io.BytesIO()
        kwargs = {"text": text, "lang": language}
        if language == "en":
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
