"""Process-local FIFO queue in front of speech-to-text.

Waiting capacity (``queue_max``) is counted separately from active transcriptions
(``max_parallel``). Admission checks are atomic under an asyncio lock so concurrent
requests cannot overfill the waiting queue. Slots are released on success,
exception, timeout, and cancellation.

This queue is process-local (one uvicorn worker). It is not shared across
processes or hosts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
NotifyFn = Callable[[], Awaitable[None]]


class TranscriptionQueueFull(Exception):
    """Waiting queue is at ``queue_max``; the job was not admitted."""


class TranscriptionQueueClosed(Exception):
    """Queue is shut down and no longer accepts work."""


@dataclass
class _Waiter:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    promoted: bool = False
    discarded: bool = False


@dataclass(frozen=True)
class QueueAdmission:
    """Snapshot taken after a successful admit."""

    waiting: int
    active: int
    notify: bool
    wait_ms: int = 0


class TranscriptionQueue:
    """Bounded FIFO gate for CPU-bound transcription work.

    ``queue_max`` limits *waiting* jobs only (not active ones).
    ``notify_at`` is compared to the waiting count *after* admission (waiting only);
    notification fires when ``waiting >= notify_at``.
    """

    def __init__(self, *, max_parallel: int = 1, queue_max: int = 16, notify_at: int = 2):
        if max_parallel < 1:
            raise ValueError("max_parallel must be >= 1")
        if queue_max < 0:
            raise ValueError("queue_max must be >= 0")
        if notify_at < 1:
            raise ValueError("notify_at must be >= 1")
        self.max_parallel = max_parallel
        self.queue_max = queue_max
        self.notify_at = notify_at
        self._lock = asyncio.Lock()
        self._waiting: deque[_Waiter] = deque()
        self._active = 0
        self._closed = False

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return len(self._waiting)

    async def run(
        self,
        work: Callable[[], Awaitable[T]],
        *,
        on_notify: NotifyFn | None = None,
    ) -> tuple[T, QueueAdmission]:
        """Admit, optionally notify, wait FIFO for a slot, run ``work``, release.

        Returns ``(result, admission)`` where ``admission.wait_ms`` is the time
        spent waiting for a parallel slot after admission.
        """
        waiter, admission = await self._admit()
        logger.info(
            "voice_queue_admit waiting=%s active=%s notify=%s",
            admission.waiting,
            admission.active,
            admission.notify,
        )
        if admission.notify and on_notify is not None:
            try:
                await on_notify()
            except Exception:
                logger.warning("voice_queue_notify_failed", exc_info=True)

        wait_ms = 0
        if waiter is not None:
            wait_started = time.monotonic()
            try:
                await self._await_waiter(waiter)
            except BaseException:
                raise
            wait_ms = round((time.monotonic() - wait_started) * 1000)
            if waiter.discarded:
                raise TranscriptionQueueClosed("Voice transcription queue is closed")

        admission = QueueAdmission(
            waiting=admission.waiting,
            active=admission.active,
            notify=admission.notify,
            wait_ms=wait_ms,
        )
        logger.info(
            "voice_stt_start wait_ms=%s waiting=%s active=%s",
            wait_ms,
            self.waiting,
            self.active,
        )
        stt_started = time.monotonic()
        try:
            result = await work()
            return result, admission
        finally:
            stt_ms = round((time.monotonic() - stt_started) * 1000)
            logger.info("voice_stt_end stt_ms=%s wait_ms=%s", stt_ms, wait_ms)
            await self._release()

    async def _admit(self) -> tuple[_Waiter | None, QueueAdmission]:
        async with self._lock:
            if self._closed:
                raise TranscriptionQueueClosed("Voice transcription queue is closed")
            if self._active < self.max_parallel:
                self._active += 1
                return None, QueueAdmission(
                    waiting=len(self._waiting),
                    active=self._active,
                    notify=False,
                )
            if len(self._waiting) >= self.queue_max:
                raise TranscriptionQueueFull(
                    f"Voice waiting queue is full ({self.queue_max})"
                )
            waiter = _Waiter()
            self._waiting.append(waiter)
            waiting = len(self._waiting)
            return waiter, QueueAdmission(
                waiting=waiting,
                active=self._active,
                notify=waiting >= self.notify_at,
            )

    async def _await_waiter(self, waiter: _Waiter) -> None:
        try:
            await waiter.event.wait()
        except asyncio.CancelledError:
            async with self._lock:
                if not waiter.promoted and waiter in self._waiting:
                    self._waiting.remove(waiter)
                elif waiter.promoted and not waiter.discarded:
                    # Received a slot then cancelled before work started.
                    await self._release_locked()
            raise

    async def _release(self) -> None:
        async with self._lock:
            await self._release_locked()

    async def _release_locked(self) -> None:
        while self._waiting:
            nxt = self._waiting.popleft()
            if nxt.discarded:
                continue
            nxt.promoted = True
            # Slot transfers to the next waiter; active count unchanged.
            nxt.event.set()
            return
        if self._active > 0:
            self._active -= 1

    async def close(self) -> None:
        """Stop admissions and fail waiters so they do not hang forever."""
        async with self._lock:
            self._closed = True
            waiters = list(self._waiting)
            self._waiting.clear()
            for waiter in waiters:
                waiter.discarded = True
                waiter.promoted = True
                waiter.event.set()
