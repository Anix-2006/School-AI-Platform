from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.voice.service import VoiceService
from app.voice.stt import get_stt
from app.voice.storage import AudioStore
from app.voice.tts import get_tts

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
                stt = get_stt()
                # Load the Whisper model at app startup, not on the first voice request.
                await asyncio.to_thread(stt.warmup)
                tts = get_tts()
                self.service = VoiceService(
                    stt,
                    tts,
                    AudioStore(ttl_seconds=settings.voice_audio_ttl),
                    max_concurrency=settings.voice_max_concurrency,
                    queue_timeout=settings.voice_queue_timeout,
                    guardian_rate=settings.voice_guardian_rate,
                    global_rate=settings.voice_global_rate,
                    speak_rate=settings.voice_speak_rate,
                    max_seconds=settings.voice_max_seconds,
                    tts_timeout=settings.tts_timeout_seconds,
                    tts_retries=settings.tts_retries,
                    tts_retry_delay=settings.tts_retry_delay_seconds,
                    tts_cooldown=settings.tts_cooldown_seconds,
                    tts_cooldown_failures=settings.tts_cooldown_failures,
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
