from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class _StoredAudio:
    owner_id: str
    data: bytes
    expires_at: float


class AudioStore:
    """Process-local, expiring audio. Successful reads consume the item."""

    def __init__(self, ttl_seconds: float = 300, max_items: int = 256):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: dict[str, _StoredAudio] = {}
        self._lock = threading.Lock()

    def put(self, owner_id: str, data: bytes) -> str:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            while len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda key: self._items[key].expires_at)
                self._items.pop(oldest)
            audio_id = secrets.token_urlsafe(24)
            self._items[audio_id] = _StoredAudio(owner_id, data, now + self.ttl_seconds)
            return audio_id

    def take(self, audio_id: str, owner_id: str) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            item = self._items.get(audio_id)
            if item is None or not secrets.compare_digest(item.owner_id, owner_id):
                return None
            self._items.pop(audio_id)
            return item.data

    def _purge(self, now: float) -> None:
        for audio_id, item in list(self._items.items()):
            if item.expires_at <= now:
                self._items.pop(audio_id, None)
