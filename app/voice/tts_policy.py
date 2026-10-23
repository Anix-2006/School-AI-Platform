"""Time limit, one retry, and a cool-down for speech generation.

A hung gTTS call is abandoned when the time limit is reached. The thread may
still be running, but the caller is not waiting for it. After several failures
in a row, later calls are skipped until the cool-down ends.
"""

from __future__ import annotations

import asyncio
import threading
import time

from app.config import settings


_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None
_circuit_guard = threading.Lock()


def _attempt_slots() -> threading.BoundedSemaphore:
    """Cap hung gTTS calls so a long outage cannot start a thread every time."""
    global _slots
    with _slots_lock:
        if _slots is None:
            width = max(1, positive_int(settings.tts_max_parallel, 3) or 1)
            extra = positive_int(settings.tts_retries, 1) + 1
            _slots = threading.BoundedSemaphore(max(4, width * extra))
        return _slots


def reserve_attempt_slot() -> threading.BoundedSemaphore | None:
    """Return the semaphore when a slot was taken, or None when none are free."""
    slots = _attempt_slots()
    if not slots.acquire(blocking=False):
        return None
    return slots


class TtsCircuit:
    """Skip speech for a while after too many failures in a row."""

    def __init__(self, *, failures_before_open: int, cooldown_seconds: float):
        self.failures_before_open = max(1, int(failures_before_open))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def cooling_down(self) -> bool:
        with self._lock:
            return time.monotonic() < self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failures_before_open:
                self._open_until = time.monotonic() + self.cooldown_seconds
                self._failures = 0


def circuit_for(speaker: object) -> TtsCircuit:
    """One cool-down tracker per speech engine, shared by later calls."""
    existing = getattr(speaker, "_tts_circuit", None)
    if isinstance(existing, TtsCircuit):
        return existing
    with _circuit_guard:
        existing = getattr(speaker, "_tts_circuit", None)
        if isinstance(existing, TtsCircuit):
            return existing
        circuit = TtsCircuit(
            failures_before_open=max(1, positive_int(settings.tts_cooldown_failures, 3) or 1),
            cooldown_seconds=non_negative_float(settings.tts_cooldown_seconds, 30.0),
        )
        try:
            setattr(speaker, "_tts_circuit", circuit)
        except Exception:
            return circuit
        return circuit


def speak_bounded(
    speaker: object,
    text: str,
    language: str,
    circuit: TtsCircuit,
    *,
    timeout: float,
    retries: int,
    retry_delay: float,
) -> tuple[bytes, bool]:
    """Speak with a time limit and retries. ``(audio, failed)``."""
    if circuit.cooling_down():
        return b"", True
    attempts = positive_int(retries, 0) + 1
    for attempt in range(attempts):
        if circuit.cooling_down():
            return b"", True
        try:
            audio = run_blocking_sync(lambda: speaker.speak(text, language), timeout)
        except ValueError:
            raise
        except Exception:
            circuit.record_failure()
            if attempt + 1 < attempts:
                if retry_delay > 0:
                    time.sleep(retry_delay)
                continue
            return b"", True
        circuit.record_success()
        return audio or b"", False
    return b"", True


async def run_blocking(function, timeout: float):
    """Run a blocking speech call without waiting past ``timeout``."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    active = threading.Event()
    active.set()

    def work() -> None:
        try:
            outcome = ("ok", function())
        except Exception as exc:
            outcome = ("err", exc)
        post_if_active(loop, queue, outcome, active)

    threading.Thread(target=work, name="tts-attempt", daemon=True).start()
    try:
        kind, payload = await asyncio.wait_for(queue.get(), timeout)
    except TimeoutError as exc:
        active.clear()
        raise TimeoutError("Speech generation timed out") from exc
    if kind == "err":
        raise payload
    return payload


def post_if_active(loop, queue, item, active: threading.Event | None = None) -> None:
    """Hand a result to the request that is still waiting for it.

    A timed-out request clears ``active``. A closed loop is ignored so the
    worker thread does not raise after the caller has already moved on.
    """
    if active is not None and not active.is_set():
        return
    try:
        if loop.is_closed():
            return
        loop.call_soon_threadsafe(queue.put_nowait, item)
    except RuntimeError as exc:
        if "closed" not in str(exc).lower():
            raise


def run_blocking_sync(function, timeout: float, *, limited: bool = False):
    """Blocking form of :func:`run_blocking` for one gTTS call or the pipeline.

    ``limited`` refuses the call when too many gTTS attempts are already hung,
    instead of starting another thread.
    """
    slot = reserve_attempt_slot() if limited else None
    if limited and slot is None:
        raise TimeoutError("Speech generation is already at capacity")
    finished = threading.Event()
    outcome: dict = {}

    def work() -> None:
        try:
            try:
                outcome["ok"] = function()
            except Exception as exc:
                outcome["err"] = exc
            finished.set()
        finally:
            if slot is not None:
                slot.release()

    threading.Thread(target=work, name="tts-attempt", daemon=True).start()
    if not finished.wait(timeout):
        raise TimeoutError("Speech generation timed out")
    if "err" in outcome:
        raise outcome["err"]
    return outcome.get("ok")


def positive_int(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number >= 0 else fallback


def timeout_seconds(value, fallback: float = 5.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def non_negative_float(value, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number >= 0 else fallback
