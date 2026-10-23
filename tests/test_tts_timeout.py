"""gTTS time limit, one retry, text fallback, and cool-down (issue #52)."""

import asyncio
import logging
import threading
import time
import wave
from pathlib import Path

import pytest

from app.config import Settings
from app.voice.service import VoiceService
from app.voice.stt import Transcript
from app.voice.storage import AudioStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "three_questions.wav"


class FakeStt:
    def transcribe(self, audio_wav, language=None, initial_prompt=None):
        return Transcript("Any homework?", language or "en", duration_seconds=1.0, confidence=1.0)


class ScriptedTts:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def speak(self, text, language):
        self.calls += 1
        outcome = self._results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class HangingTts:
    def __init__(self):
        self.release = threading.Event()
        self.calls = 0

    def speak(self, text, language):
        self.calls += 1
        self.release.wait(timeout=30)
        return b"ID3late"


def _chat(service):
    return asyncio.run(
        service.chat(
            tenant_id="t-1",
            guardian={"id": "g-1", "preferred_language": "en"},
            student={"id": "s-1"},
            audio=b"audio",
            content_type="audio/wav",
            language="en",
            session_id="timeout",
        )
    )


def _service(tts, **kwargs):
    options = {
        "tts_timeout": 0.2,
        "tts_retries": 1,
        "tts_retry_delay": 0,
        "tts_cooldown": 30,
        "tts_cooldown_failures": 3,
    }
    options.update(kwargs)
    return VoiceService(FakeStt(), tts, AudioStore(), **options)


def test_timeout_and_retry_settings_have_the_issue_defaults():
    assert Settings.model_fields["tts_timeout_seconds"].default == 5
    assert Settings.model_fields["tts_retries"].default == 1
    assert Settings.model_fields["tts_cooldown_seconds"].default == 30
    assert Settings.model_fields["tts_cooldown_failures"].default == 3


def test_one_failure_then_success_gives_audio(monkeypatch):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "The school is closed tomorrow.", "student_id": "s-1"},
    )
    tts = ScriptedTts([RuntimeError("blip"), b"ID3ok"])
    result = _chat(_service(tts))
    assert tts.calls == 2
    assert result["reply"] == "The school is closed tomorrow."
    assert result["audio_id"]
    assert result["tts_degraded"] is False
    assert result["error_stage"] is None


def test_two_failures_give_text_only(monkeypatch, caplog):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "Bring the homework sheet.", "student_id": "s-1"},
    )
    tts = ScriptedTts([RuntimeError("down"), RuntimeError("still down")])
    caplog.set_level(logging.INFO)
    result = _chat(_service(tts))
    assert tts.calls == 2
    assert result["reply"] == "Bring the homework sheet."
    assert result["audio_id"] is None
    assert result["tts_degraded"] is True
    assert result["error_stage"] == "tts"
    assert "error_stage=tts" in caplog.text


def test_a_hang_is_cut_off_at_the_timeout(monkeypatch):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "The bus leaves at four.", "student_id": "s-1"},
    )
    tts = HangingTts()
    started = time.perf_counter()
    try:
        result = _chat(_service(tts, tts_timeout=0.15, tts_retries=1))
    finally:
        tts.release.set()
    elapsed = time.perf_counter() - started
    assert tts.calls == 2
    assert elapsed < 1.2
    assert result["reply"] == "The bus leaves at four."
    assert result["audio_id"] is None
    assert result["error_stage"] == "tts"


def test_cool_down_skips_speech_calls(monkeypatch):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "School is closed tomorrow.", "student_id": "s-1"},
    )
    tts = ScriptedTts([RuntimeError("down")] * 5)
    service = _service(tts, tts_retries=0, tts_cooldown_failures=2, tts_cooldown=60)
    first = _chat(service)
    second = _chat(service)
    started = time.perf_counter()
    third = _chat(service)
    elapsed = time.perf_counter() - started
    assert tts.calls == 2
    assert elapsed < 0.5
    assert first["reply"] == second["reply"] == third["reply"]
    assert third["audio_id"] is None
    assert third["error_stage"] == "tts"


def test_live_sends_the_text_when_speech_fails(client, monkeypatch):
    from app.main import app
    from app.services import chat_service
    from app.voice.vad import segmenter_from_settings
    from tests.fakes import FakeGraph

    monkeypatch.setattr(chat_service, "orchestrator_graph", FakeGraph(reply="Bring the homework sheet."))

    class DownTts:
        SUPPORTED_LANGUAGES = {"en", "hi", "te"}

        def __init__(self):
            self.calls = 0

        def speak(self, text, language):
            self.calls += 1
            raise RuntimeError("gTTS unreachable")

    class Hearing:
        def transcribe(self, audio_wav, language=None, initial_prompt=None):
            return Transcript("Any homework?", "en", duration_seconds=0.4, confidence=1.0)

    class Ready:
        state = "ready"

        def __init__(self, service):
            self.service = service

    tts = DownTts()
    previous = getattr(app.state, "voice_runtime", None)
    app.state.voice_runtime = Ready(
        VoiceService(
            Hearing(),
            tts,
            AudioStore(),
            tts_timeout=0.3,
            tts_retries=1,
            tts_retry_delay=0,
            tts_cooldown_failures=5,
        )
    )
    try:
        with wave.open(str(FIXTURE), "rb") as handle:
            pcm = handle.readframes(handle.getnframes())
        utterance = segmenter_from_settings().push(pcm).utterances[0]
        token = client.headers["Authorization"].split(" ", 1)[1]
        with client.websocket_connect(f"/voice/live?access_token={token}") as ws:
            ws.send_json(
                {
                    "type": "start",
                    "guardian_id": "g-3-1",
                    "session_id": "text-fallback",
                    "sample_rate": 16000,
                }
            )
            seen = []
            ws.send_bytes(utterance)
            reply = None
            for _ in range(12):
                message = ws.receive_json()
                seen.append(message)
                if message.get("type") == "reply":
                    reply = message
                    break
            follow = ws.receive_json()
        assert reply is not None, seen
        assert reply["reply"] == "Bring the homework sheet."
        assert reply["audio_base64"] is None
        assert not any(item.get("type") == "error" for item in seen)
        assert follow.get("type") == "status"
        assert follow.get("state") == "listening"
        assert tts.calls == 2
    finally:
        app.state.voice_runtime = previous


def test_retry_count_stays_bounded(monkeypatch):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "Bring the homework sheet.", "student_id": "s-1"},
    )
    tts = ScriptedTts([RuntimeError("down")] * 6)
    result = _chat(_service(tts, tts_retries=1))
    assert tts.calls == 2
    assert result["reply"] == "Bring the homework sheet."
    assert result["audio_id"] is None


def test_cool_down_expires_and_allows_another_attempt(monkeypatch):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "School is closed tomorrow.", "student_id": "s-1"},
    )
    tts = ScriptedTts([RuntimeError("down"), RuntimeError("down again")])
    service = _service(tts, tts_retries=0, tts_cooldown_failures=1, tts_cooldown=0.2)
    first = _chat(service)
    skipped = _chat(service)
    assert tts.calls == 1
    assert skipped["audio_id"] is None
    time.sleep(0.25)
    _chat(service)
    assert tts.calls == 2
    assert first["reply"] == skipped["reply"]


def test_success_resets_consecutive_failures(monkeypatch):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "The bus leaves at four.", "student_id": "s-1"},
    )
    tts = ScriptedTts(
        [RuntimeError("blip"), b"ID3ok", RuntimeError("blip"), RuntimeError("blip")]
    )
    service = _service(tts, tts_retries=0, tts_cooldown_failures=2, tts_cooldown=60)
    failed = _chat(service)
    spoken = _chat(service)
    _chat(service)
    _chat(service)
    skipped = _chat(service)
    assert failed["audio_id"] is None
    assert spoken["audio_id"]
    assert tts.calls == 4
    assert skipped["audio_id"] is None
    assert skipped["error_stage"] == "tts"


def test_transcription_and_answer_failures_are_not_tts_errors(monkeypatch, caplog):
    from app.voice import service as module

    class BoomStt:
        def transcribe(self, audio_wav, language=None, initial_prompt=None):
            raise RuntimeError("whisper failed")

    tts = ScriptedTts([b"ID3ok"])
    caplog.set_level(logging.INFO)
    with pytest.raises(RuntimeError, match="whisper failed"):
        _chat(VoiceService(BoomStt(), tts, AudioStore(), tts_retries=0, tts_retry_delay=0))
    assert tts.calls == 0
    assert "error_stage=tts" not in caplog.text

    def fail_answer(*_args):
        raise RuntimeError("llm failed")

    monkeypatch.setattr(module, "run_parent_turn", fail_answer)
    with pytest.raises(RuntimeError, match="llm failed"):
        _chat(_service(tts, tts_retries=0))
    assert tts.calls == 0
    assert "error_stage=tts" not in caplog.text


def test_late_worker_result_does_not_raise_after_timeout():
    from app.voice.tts_policy import run_blocking

    release = threading.Event()
    finished = threading.Event()

    def hang():
        try:
            release.wait(timeout=2)
            return b"late"
        finally:
            finished.set()

    async def attempt():
        with pytest.raises(TimeoutError):
            await run_blocking(hang, 0.05)

    asyncio.run(attempt())
    release.set()
    assert finished.wait(timeout=1)
    time.sleep(0.05)


def test_capacity_limit_skips_extra_hung_gtts_calls():
    import app.voice.tts_policy as policy

    previous = policy._slots
    policy._slots = threading.BoundedSemaphore(1)
    started = threading.Event()
    release = threading.Event()

    def hang():
        started.set()
        release.wait(timeout=2)
        return b"ID3"

    held = policy.reserve_attempt_slot()
    assert held is not None
    try:
        with pytest.raises(TimeoutError, match="capacity"):
            policy.run_blocking_sync(hang, 1, limited=True)
        assert not started.is_set()
    finally:
        held.release()
        release.set()
        policy._slots = previous


def test_each_gtts_call_stops_at_the_timeout_and_is_not_cached(monkeypatch):
    import gtts

    from app.config import settings
    from app.voice.tts import GTTSSpeaker

    monkeypatch.setattr(settings, "tts_timeout_seconds", 0.1)
    release = threading.Event()

    class Slow:
        def __init__(self, *args, **kwargs):
            pass

        def write_to_fp(self, target):
            release.wait(timeout=5)
            target.write(b"ID3slow")

    monkeypatch.setattr(gtts, "gTTS", Slow)
    speaker = GTTSSpeaker(cache_size=2)
    started = time.perf_counter()
    try:
        with pytest.raises(TimeoutError):
            speaker.speak("School is closed tomorrow.", "en")
    finally:
        release.set()
    assert time.perf_counter() - started < 1.0
    assert list(speaker._cache) == []


def test_empty_audio_is_not_cached(monkeypatch):
    from app.voice.tts import GTTSSpeaker

    speaker = GTTSSpeaker(cache_size=2)
    monkeypatch.setattr(speaker, "_render", lambda text, lang: b"")
    with pytest.raises(RuntimeError, match="no audio"):
        speaker.speak("School is closed tomorrow.", "en")
    assert list(speaker._cache) == []


def test_concurrent_failures_open_the_cooldown_once():
    from app.voice.tts_policy import TtsCircuit

    circuit = TtsCircuit(failures_before_open=3, cooldown_seconds=30)
    threads = [threading.Thread(target=circuit.record_failure) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert circuit.cooling_down()
    circuit.record_success()
    assert not circuit.cooling_down()
