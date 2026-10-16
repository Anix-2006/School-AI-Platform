from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.voice.service import VoiceService
from app.voice.stt import FasterWhisperEngine
from app.voice.storage import AudioStore
from app.voice.tts import GttsEngine

logger = logging.getLogger(__name__)


class VoiceRuntime:
    def __init__(self):
        self.state = "disabled" if not settings.voice_enabled else "warming"
        self.error: str | None = None
        self.service: VoiceService | None = None
        self._warm_lock = asyncio.Lock()

    async def warm(self) -> None:
        if not settings.voice_enabled or self.state == "ready":
            return
        async with self._warm_lock:
            if self.state == "ready":
                return
            self.state = "warming"
            try:
                stt = FasterWhisperEngine(
                    settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                )
                await asyncio.to_thread(stt.warmup)
                tts = GttsEngine(
                    timeout=settings.voice_tts_timeout,
                    cache_size=settings.tts_cache_size,
                )
                self.service = VoiceService(
                    stt,
                    tts,
                    AudioStore(ttl_seconds=settings.voice_audio_ttl),
                    max_parallel=settings.voice_max_parallel,
                    max_concurrency=settings.voice_max_concurrency,
                    queue_max=settings.voice_queue_max,
                    queue_notify_at=settings.voice_queue_notify_at,
                    queue_timeout=settings.voice_queue_timeout,
                    guardian_rate=settings.voice_guardian_rate,
                    global_rate=settings.voice_global_rate,
                    speak_rate=settings.voice_speak_rate,
                    max_seconds=settings.voice_max_seconds,
                )
                self.state = "ready"
                self.error = None
            except Exception as exc:
                logger.exception("Voice runtime failed to warm")
                self.state = "error"
                self.error = str(exc)

    def status(self) -> dict:
        return {"status": self.state}

    def close(self) -> None:
        if self.service is not None:
            self.service.close()
            self.service = None
