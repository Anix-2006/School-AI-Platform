"""Bounded FIFO transcription queue (issue #54)."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.voice.service import (
    VoiceError,
    VoiceService,
    queue_busy_phrase,
    queue_status_phrase,
)
from app.voice.storage import AudioStore
from app.voice.transcription_queue import (
    TranscriptionQueue,
    TranscriptionQueueFull,
)
from tests.test_voice import FakeStt, FakeTts


def _chat_kwargs(guardian_id="g-1", language="en", **extra):
    base = {
        "tenant_id": "t-1",
        "guardian": {"id": guardian_id, "preferred_language": language},
        "student": {"id": "s-1", "name": "Ananya"},
        "audio": b"audio",
        "content_type": "audio/wav",
        "language": language,
        "session_id": None,
    }
    base.update(extra)
    return base


def _service(
    stt=None,
    *,
    max_parallel=1,
    queue_max=16,
    queue_notify_at=2,
    notifier=None,
    monkeypatch=None,
):
    if monkeypatch is not None:
        from app.voice import service as module

        monkeypatch.setattr(
            module,
            "run_parent_turn",
            lambda *_a, **_k: {
                "reply": "Answer",
                "student_id": "s-1",
                "language": "en",
                "agent_used": "parent_teacher",
            },
        )
    return VoiceService(
        stt or FakeStt(delay=0.05),
        FakeTts(),
        AudioStore(),
        max_parallel=max_parallel,
        queue_max=queue_max,
        queue_notify_at=queue_notify_at,
        queue_notifier=notifier,
        guardian_rate=1000,
        global_rate=1000,
    )


# --- A / B: FIFO + parallel limits -------------------------------------------------


def test_five_requests_run_fifo_without_overlapping_stt(monkeypatch):
    order: list[str] = []
    active = 0
    max_active = 0
    gate = asyncio.Event()
    started = 0

    class TrackingStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            nonlocal active, max_active, started
            self.calls += 1
            label = audio.decode()
            active += 1
            max_active = max(max_active, active)
            started += 1
            if started == 1:
                gate.set()
            order.append(f"start:{label}")
            time.sleep(0.03)
            order.append(f"end:{label}")
            active -= 1
            return self.result

    service = _service(
        TrackingStt(), max_parallel=1, queue_max=10, monkeypatch=monkeypatch
    )

    async def run():
        tasks = [
            asyncio.create_task(
                service.chat(**_chat_kwargs(audio=str(i).encode())),
                name=f"job-{i}",
            )
            for i in range(5)
        ]
        await gate.wait()
        results = await asyncio.gather(*tasks)
        assert len(results) == 5
        assert all(r["reply"] == "Answer" for r in results)

    asyncio.run(run())
    assert max_active == 1
    assert [e for e in order if e.startswith("start:")] == [
        "start:0",
        "start:1",
        "start:2",
        "start:3",
        "start:4",
    ]
    # Each job finishes before the next starts when parallel=1.
    for i in range(5):
        assert order.index(f"start:{i}") < order.index(f"end:{i}")
        if i < 4:
            assert order.index(f"end:{i}") < order.index(f"start:{i + 1}")


def test_higher_parallel_limit_allows_overlap(monkeypatch):
    active = 0
    max_active = 0
    release = asyncio.Event()
    entered = 0
    entered_event = asyncio.Event()

    class BarrierStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            nonlocal active, max_active, entered
            self.calls += 1
            active += 1
            max_active = max(max_active, active)
            entered += 1
            if entered >= 2:
                entered_event.set()
            # Block the worker thread until the async test releases.
            while not release.is_set():
                time.sleep(0.005)
            active -= 1
            return self.result

    service = _service(
        BarrierStt(), max_parallel=2, queue_max=4, monkeypatch=monkeypatch
    )

    async def run():
        t1 = asyncio.create_task(service.chat(**_chat_kwargs(guardian_id="g-1")))
        t2 = asyncio.create_task(service.chat(**_chat_kwargs(guardian_id="g-2")))
        await asyncio.wait_for(entered_event.wait(), timeout=2)
        assert max_active == 2
        release.set()
        await asyncio.gather(t1, t2)

    asyncio.run(run())
    assert max_active == 2


# --- C: notify once at threshold --------------------------------------------------


def test_notify_exactly_once_at_threshold(monkeypatch):
    import threading

    notices: list[tuple[str, str]] = []
    stt_hold = threading.Event()
    first_entered = threading.Event()

    class HoldStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            self.calls += 1
            first_entered.set()
            stt_hold.wait(timeout=2)
            return self.result

    async def notifier(guardian_id, phrase):
        notices.append((guardian_id, phrase))

    service = _service(
        HoldStt(),
        max_parallel=1,
        queue_max=10,
        queue_notify_at=2,
        notifier=notifier,
        monkeypatch=monkeypatch,
    )

    async def run():
        # Job 0 active; jobs 1 then 2 waiting. Notify when waiting reaches 2.
        t0 = asyncio.create_task(service.chat(**_chat_kwargs(guardian_id="g-0")))
        await asyncio.to_thread(first_entered.wait, 2)
        t1 = asyncio.create_task(service.chat(**_chat_kwargs(guardian_id="g-1")))
        await asyncio.sleep(0.02)
        assert notices == []
        t2 = asyncio.create_task(service.chat(**_chat_kwargs(guardian_id="g-2")))
        await asyncio.sleep(0.05)
        assert len(notices) == 1
        assert notices[0][0] == "g-2"
        assert notices[0][1] == queue_status_phrase("en")
        stt_hold.set()
        results = await asyncio.gather(t0, t1, t2)
        assert results[2].get("queue_notice") == queue_status_phrase("en")
        assert results[0].get("queue_notice") is None
        assert results[1].get("queue_notice") is None

    asyncio.run(run())
    assert len(notices) == 1


def test_notify_boundary_and_below_threshold(monkeypatch):
    notices: list[str] = []
    import threading

    hold = threading.Event()
    entered = threading.Event()

    class HoldStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            self.calls += 1
            entered.set()
            hold.wait(timeout=2)
            return self.result

    async def notifier(guardian_id, phrase):
        notices.append(phrase)

    # notify_at=1 → first waiting job notifies
    service = _service(
        HoldStt(),
        max_parallel=1,
        queue_max=5,
        queue_notify_at=1,
        notifier=notifier,
        monkeypatch=monkeypatch,
    )

    async def run():
        t0 = asyncio.create_task(service.chat(**_chat_kwargs()))
        await asyncio.to_thread(entered.wait, 2)
        t1 = asyncio.create_task(service.chat(**_chat_kwargs(guardian_id="g-wait")))
        await asyncio.sleep(0.05)
        assert len(notices) == 1
        hold.set()
        await asyncio.gather(t0, t1)

    asyncio.run(run())


# --- D / E: full queue + concurrent admission -------------------------------------


def test_full_queue_returns_localized_busy_without_transcribing(monkeypatch):
    import threading

    hold = threading.Event()
    entered = threading.Event()

    class HoldStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            self.calls += 1
            entered.set()
            hold.wait(timeout=2)
            return self.result

    held = HoldStt()
    service = _service(
        held,
        max_parallel=1,
        queue_max=1,
        queue_notify_at=10,
        monkeypatch=monkeypatch,
    )

    async def run():
        t0 = asyncio.create_task(service.chat(**_chat_kwargs(language="hi")))
        await asyncio.to_thread(entered.wait, 2)
        t1 = asyncio.create_task(
            service.chat(**_chat_kwargs(guardian_id="g-1", language="hi"))
        )
        await asyncio.sleep(0.02)
        with pytest.raises(VoiceError) as busy:
            await service.chat(**_chat_kwargs(guardian_id="g-2", language="hi"))
        assert busy.value.status_code == 429
        assert busy.value.detail == queue_busy_phrase("hi")
        hold.set()
        await asyncio.gather(t0, t1)

    asyncio.run(run())
    assert held.calls == 2  # rejected job never transcribed


def test_simultaneous_admission_cannot_exceed_queue_capacity():
    queue = TranscriptionQueue(max_parallel=1, queue_max=2, notify_at=10)
    hold = asyncio.Event()
    started = 0
    start_gate = asyncio.Event()

    async def work():
        nonlocal started
        started += 1
        if started == 1:
            start_gate.set()
        await hold.wait()
        return "ok"

    async def run():
        # Fill: 1 active + 2 waiting.
        tasks = [asyncio.create_task(queue.run(work)) for _ in range(3)]
        await start_gate.wait()
        await asyncio.sleep(0.02)
        assert queue.active == 1 and queue.waiting == 2
        overflow = await asyncio.gather(
            *[queue.run(work) for _ in range(8)],
            return_exceptions=True,
        )
        assert sum(isinstance(exc, TranscriptionQueueFull) for exc in overflow) == 8
        hold.set()
        await asyncio.gather(*tasks)
        assert queue.active == 0 and queue.waiting == 0

    asyncio.run(run())


# --- F / G: exceptions, cancel, notify failure ------------------------------------


def test_transcription_exception_releases_capacity(monkeypatch):
    calls = 0

    class FlakyStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("stt boom")
            return self.result

    service = _service(FlakyStt(), max_parallel=1, queue_max=4, monkeypatch=monkeypatch)

    async def run():
        with pytest.raises(RuntimeError):
            await service.chat(**_chat_kwargs())
        result = await service.chat(**_chat_kwargs(guardian_id="g-2"))
        assert result["reply"] == "Answer"

    asyncio.run(run())
    assert calls == 2


def test_cancellation_does_not_leak_capacity(monkeypatch):
    import threading

    hold = threading.Event()
    entered = threading.Event()

    class HoldStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            self.calls += 1
            entered.set()
            hold.wait(timeout=2)
            return self.result

    service = _service(
        HoldStt(), max_parallel=1, queue_max=4, monkeypatch=monkeypatch
    )

    async def run():
        t0 = asyncio.create_task(service.chat(**_chat_kwargs()))
        await asyncio.to_thread(entered.wait, 2)
        waiting = asyncio.create_task(
            service.chat(**_chat_kwargs(guardian_id="g-wait"))
        )
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        hold.set()
        await t0
        # Slot still usable.
        assert (await service.chat(**_chat_kwargs(guardian_id="g-later")))["reply"] == "Answer"
        assert service._queue.active == 0
        assert service._queue.waiting == 0

    asyncio.run(run())


def test_notify_failure_does_not_duplicate_or_drop_transcription(monkeypatch):
    import threading

    hold = threading.Event()
    entered = threading.Event()
    stt = FakeStt()

    class HoldStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            self.calls += 1
            stt.calls += 1
            entered.set()
            hold.wait(timeout=2)
            return self.result

    async def bad_notifier(guardian_id, phrase):
        raise RuntimeError("send failed")

    service = _service(
        HoldStt(),
        max_parallel=1,
        queue_max=4,
        queue_notify_at=1,
        notifier=bad_notifier,
        monkeypatch=monkeypatch,
    )

    async def run():
        t0 = asyncio.create_task(service.chat(**_chat_kwargs()))
        await asyncio.to_thread(entered.wait, 2)
        t1 = asyncio.create_task(service.chat(**_chat_kwargs(guardian_id="g-1")))
        await asyncio.sleep(0.05)
        hold.set()
        results = await asyncio.gather(t0, t1)
        assert all(r["reply"] == "Answer" for r in results)
        assert stt.calls == 2

    asyncio.run(run())


# --- H: logging -------------------------------------------------------------------


def test_queue_and_stt_durations_logged(monkeypatch, caplog):
    service = _service(
        FakeStt(delay=0.02), max_parallel=1, queue_max=4, monkeypatch=monkeypatch
    )
    with caplog.at_level(logging.INFO, logger="app.voice.transcription_queue"):
        asyncio.run(service.chat(**_chat_kwargs()))
    messages = [record.getMessage() for record in caplog.records]
    text = " ".join(messages)
    assert "voice_queue_admit" in text
    assert "voice_stt_start" in text
    assert "voice_stt_end" in text
    assert "wait_ms=" in text
    assert "sk-" not in text
    assert "Bearer" not in text


# --- I: configuration -------------------------------------------------------------


def test_queue_settings_defaults_and_validation(monkeypatch):
    for name in (
        "VOICE_MAX_PARALLEL",
        "VOICE_MAX_CONCURRENCY",
        "VOICE_QUEUE_MAX",
        "VOICE_QUEUE_NOTIFY_AT",
    ):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(openai_api_key="x", _env_file=None)
    assert defaults.voice_max_parallel == 1
    assert defaults.voice_queue_max == 16
    assert defaults.voice_queue_notify_at == 2

    monkeypatch.setenv("VOICE_MAX_CONCURRENCY", "3")
    monkeypatch.delenv("VOICE_MAX_PARALLEL", raising=False)
    aliased = Settings(openai_api_key="x", _env_file=None)
    assert aliased.voice_max_parallel == 3

    with pytest.raises(ValidationError):
        Settings(openai_api_key="x", _env_file=None, voice_max_parallel=0)
    with pytest.raises(ValidationError):
        Settings(openai_api_key="x", _env_file=None, voice_queue_max=-1)
    with pytest.raises(ValidationError):
        Settings(openai_api_key="x", _env_file=None, voice_queue_notify_at=0)


def test_busy_and_status_phrases_cover_supported_languages():
    for lang in ("en", "hi", "te"):
        assert queue_status_phrase(lang)
        assert queue_busy_phrase(lang)
        assert queue_status_phrase(lang) != queue_busy_phrase(lang)


# --- legacy test replacement semantics --------------------------------------------


def test_short_queue_rejects_excess_waiting(monkeypatch):
    """Replaces semaphore-timeout busy behavior with waiting-queue capacity."""
    import threading

    hold = threading.Event()
    entered = threading.Event()

    class HoldStt(FakeStt):
        def transcribe(self, audio, content_type, language=None, initial_prompt=None):
            self.calls += 1
            entered.set()
            hold.wait(timeout=2)
            return self.result

    service = _service(
        HoldStt(), max_parallel=1, queue_max=0, monkeypatch=monkeypatch
    )

    async def run():
        first = asyncio.create_task(service.chat(**_chat_kwargs()))
        await asyncio.to_thread(entered.wait, 2)
        with pytest.raises(VoiceError) as busy:
            await service.chat(**_chat_kwargs(guardian_id="g-2"))
        assert busy.value.status_code == 429
        hold.set()
        await first

    asyncio.run(run())
