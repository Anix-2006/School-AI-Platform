import asyncio
import io
import sys
import time
import types
import wave

import pytest

from app.voice.audio import AudioValidationError, validate_audio
from app.voice.speech_text import TRUNCATION_NOTICE, clean_for_speech
from app.voice.service import VoiceError, VoiceService, audio_owner_key
from app.voice.stt import Transcript
from app.voice.storage import AudioStore


class FakeStt:
    def __init__(self, text="Any homework?", language="hi", delay=0):
        self.result = Transcript(text, language, 1.0, 1.0)
        self.calls = 0
        self.arguments = []
        self.delay = delay

    def load(self):
        return None

    def transcribe(self, audio, content_type, language=None, initial_prompt=None):
        self.calls += 1
        self.arguments.append((content_type, language, initial_prompt))
        time.sleep(self.delay)
        return self.result


class FakeTts:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def synthesize(self, text, language):
        self.calls.append((text, language))
        if self.fail:
            raise RuntimeError("offline")
        return b"ID3speech"


class FakeRuntime:
    state = "ready"

    def __init__(self, service):
        self.service = service

    def status(self):
        return {"status": self.state}


def wav_bytes(seconds=0.05):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * int(8000 * seconds))
    return target.getvalue()


def test_cleaner_makes_school_content_speakable():
    result = clean_for_speech("**FA1:** 42/50 on 2026-10-09 ✅")
    assert result == "formative assessment 1: 42 out of 50 on 9th October 2026"
    capped = clean_for_speech("word " * 200)
    assert len(capped) <= 400
    assert capped.endswith(TRUNCATION_NOTICE)
    assert clean_for_speech("शब्द " * 200, "hi").endswith("पूरा उत्तर स्क्रीन पर है।")
    assert clean_for_speech("పదం " * 200, "te").endswith(
        "పూర్తి సమాధానం స్క్రీన్‌పై ఉంది."
    )


def test_audio_validation_is_in_memory_and_bounded():
    validate_audio(wav_bytes(), "audio/wav", max_bytes=10000, max_seconds=1)
    with pytest.raises(AudioValidationError) as too_long:
        validate_audio(wav_bytes(0.2), "audio/wav", max_bytes=10000, max_seconds=0.1)
    assert too_long.value.status_code == 413
    with pytest.raises(AudioValidationError) as wrong_type:
        validate_audio(b"not audio", "text/plain", max_bytes=100, max_seconds=1)
    assert wrong_type.value.status_code == 415


def test_non_wav_duration_uses_in_memory_decoder(monkeypatch):
    class Container:
        duration = 2

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    fake_av = types.SimpleNamespace(
        time_base=1,
        open=lambda source, mode: (
            Container()
            if isinstance(source, io.BytesIO) and mode == "r"
            else pytest.fail("decoder did not receive in-memory bytes")
        ),
    )
    monkeypatch.setitem(sys.modules, "av", fake_av)
    with pytest.raises(AudioValidationError) as too_long:
        validate_audio(b"OggS-valid", "audio/ogg", max_bytes=100, max_seconds=1)
    assert too_long.value.status_code == 413


def test_audio_store_enforces_owner_expiry_and_one_download():
    store = AudioStore(ttl_seconds=0.02)
    owner = audio_owner_key("tenant-1", "g-1")
    audio_id = store.put(owner, b"sound")
    assert store.take(audio_id, audio_owner_key("tenant-2", "g-1")) is None
    assert store.take(audio_id, owner) == b"sound"
    assert store.take(audio_id, owner) is None
    expired = store.put(owner, b"old")
    time.sleep(0.03)
    assert store.take(expired, owner) is None


def test_silence_and_repetition_are_detected():
    from app.voice.service import _is_unusable_transcript

    assert _is_unusable_transcript(Transcript("", "en", 1, 1))
    assert _is_unusable_transcript(Transcript("hello there", "en", 0.1, 1))
    assert _is_unusable_transcript(Transcript("yes yes yes yes yes", "en", 1, 1))
    assert not _is_unusable_transcript(Transcript("Homework?", "en", 1, 1))
    assert not _is_unusable_transcript(Transcript("what is the homework today", "en", 1, 1))


def test_tts_failure_degrades_to_text(monkeypatch):
    from app.voice import service as module

    stt = FakeStt(language="te")
    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "**Answer**", "student_id": "s-1", "language": "en"},
    )
    service = VoiceService(stt, FakeTts(fail=True), AudioStore())
    result = asyncio.run(
        service.chat(
            tenant_id="t-1",
            guardian={"id": "g-1", "preferred_language": "en"},
            student={"id": "s-1"},
            audio=b"audio",
            content_type="audio/wav",
            language=None,
            session_id=None,
        )
    )
    assert result["reply"] == "**Answer**"
    assert result["tts_degraded"] is True
    assert result["audio_id"] is None
    assert stt.arguments[0][1] == "en"
    assert result["language"] == "en"


def test_silence_returns_localized_reply_without_llm(monkeypatch):
    from app.voice import service as module

    stt = FakeStt(text="", language="te")
    tts = FakeTts(fail=True)

    def unexpected_llm(*_args):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(module, "run_parent_turn", unexpected_llm)
    service = VoiceService(stt, tts, AudioStore())
    result = asyncio.run(
        service.chat(
            tenant_id="t-1",
            guardian={"id": "g-1", "preferred_language": "te"},
            student={"id": "s-1", "name": "Ananya"},
            audio=b"audio",
            content_type="audio/webm",
            language=None,
            session_id=None,
        )
    )
    assert result["agent_used"] is None
    assert result["transcript"] == ""
    assert result["language"] == "te"
    assert result["timings"]["llm"] == 0
    assert result["reply"]
    assert result["tts_degraded"] is True
    assert tts.calls


def test_pipeline_passes_content_type_student_prompt_and_timings(monkeypatch):
    from app.voice import service as module

    calls = []
    stt = FakeStt()
    original_transcribe = stt.transcribe

    def transcribe(*args):
        calls.append("stt")
        return original_transcribe(*args)

    def llm(*_args):
        calls.append("llm")
        return {"reply": "Answer", "student_id": "s-1", "language": "hi"}

    class OrderedTts(FakeTts):
        async def synthesize(self, text, language):
            calls.append("tts")
            return await super().synthesize(text, language)

    stt.transcribe = transcribe
    monkeypatch.setattr(module, "run_parent_turn", llm)
    service = VoiceService(stt, OrderedTts(), AudioStore())
    result = asyncio.run(
        service.chat(
            tenant_id="t-1",
            guardian={"id": "g-1"},
            student={"id": "s-1", "name": "Ananya"},
            audio=b"audio",
            content_type="audio/ogg",
            language="hi",
            session_id="call",
        )
    )
    assert calls == ["stt", "llm", "tts"]
    assert stt.arguments[0][0:2] == ("audio/ogg", "hi")
    assert "Ananya" in stt.arguments[0][2]
    assert all(key in result["timings"] for key in ("stt", "llm", "tts", "total"))


def test_short_queue_rejects_excess_concurrency(monkeypatch):
    from app.voice import service as module

    monkeypatch.setattr(
        module,
        "run_parent_turn",
        lambda *_args: {"reply": "Answer", "student_id": "s-1", "language": "en"},
    )
    service = VoiceService(
        FakeStt(delay=0.08), FakeTts(), AudioStore(), max_concurrency=1, queue_timeout=0.01
    )
    kwargs = {
        "tenant_id": "t-1",
        "guardian": {"id": "g-1"},
        "student": {"id": "s-1"},
        "audio": b"audio",
        "content_type": "audio/wav",
        "language": "en",
        "session_id": None,
    }
    async def run():
        first = asyncio.create_task(service.chat(**kwargs))
        await asyncio.sleep(0.005)
        with pytest.raises(VoiceError) as busy:
            await service.chat(**kwargs)
        assert busy.value.status_code == 429
        await first

    asyncio.run(run())


def test_global_and_guardian_rates_return_429():
    service = VoiceService(
        FakeStt(), FakeTts(), AudioStore(), guardian_rate=1, global_rate=2
    )
    service.check_rate("chat", "g-1")
    with pytest.raises(VoiceError) as guardian_limit:
        service.check_rate("chat", "g-1")
    assert guardian_limit.value.status_code == 429
    service.check_rate("chat", "g-2")
    with pytest.raises(VoiceError) as global_limit:
        service.check_rate("chat", "g-3")
    assert global_limit.value.status_code == 429


def test_voice_api_auth_access_history_response_and_download(
    client, monkeypatch, store
):
    import starlette.formparsers

    from app.main import app
    from app.services import chat_service
    from tests.fakes import FakeGraph

    def disk_spool_forbidden(*_args, **_kwargs):
        raise AssertionError("incoming audio attempted to spool")

    monkeypatch.setattr(
        starlette.formparsers, "SpooledTemporaryFile", disk_spool_forbidden
    )
    graph = FakeGraph()
    monkeypatch.setattr(chat_service, "orchestrator_graph", graph)
    stt = FakeStt()
    app.state.voice_runtime = FakeRuntime(VoiceService(stt, FakeTts(), AudioStore()))
    response = client.post(
        "/voice/chat",
        data={"guardian_id": "g-3-1", "student_id": "s-3-1"},
        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "transcript",
        "reply",
        "agent_used",
        "language",
        "audio_id",
        "timings",
    }
    assert set(body["timings"]) == {"queue", "stt", "llm", "tts", "total"}
    assert graph.calls[0]["state"]["channel"] == "voice"
    conversation = next(
        item
        for item in store.find("conversations", guardian_id="g-3-1")
        if item["channel"] == "voice"
    )
    messages = store.find("messages", conversation_id=conversation["id"])
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "Any homework?"),
        ("assistant", "Here is your update."),
    ]
    audio_id = body["audio_id"]
    assert client.get(f"/voice/audio/{audio_id}", params={"guardian_id": "g-7-1"}).status_code == 403
    assert client.get(f"/voice/audio/{audio_id}", params={"guardian_id": "g-3-1"}).content == b"ID3speech"
    assert client.get(f"/voice/audio/{audio_id}", params={"guardian_id": "g-3-1"}).status_code == 404


def test_access_is_resolved_before_stt(client):
    from app.main import app

    stt = FakeStt()
    app.state.voice_runtime = FakeRuntime(VoiceService(stt, FakeTts(), AudioStore()))
    response = client.post(
        "/voice/chat",
        data={"guardian_id": "g-nobody", "student_id": "s-3-1"},
        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 404
    assert stt.calls == 0


@pytest.mark.parametrize(
    ("guardian_id", "student_id", "status"),
    [
        ("g-7-1", "s-7-1", 403),
        ("g-3-1", "s-5-1", 403),
    ],
)
def test_consent_and_student_link_fail_before_stt(
    client, guardian_id, student_id, status
):
    from app.main import app

    stt = FakeStt()
    app.state.voice_runtime = FakeRuntime(VoiceService(stt, FakeTts(), AudioStore()))
    response = client.post(
        "/voice/chat",
        data={"guardian_id": guardian_id, "student_id": student_id},
        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == status
    assert stt.calls == 0


def test_voice_requires_valid_auth(client):
    from app.main import app

    app.state.voice_runtime = FakeRuntime(
        VoiceService(FakeStt(), FakeTts(), AudioStore())
    )
    authorization = client.headers.pop("Authorization")
    try:
        assert client.get("/voice/status").status_code == 401
        client.headers["Authorization"] = "Bearer invalid"
        assert client.get("/voice/status").status_code == 401
    finally:
        client.headers["Authorization"] = authorization


def test_voice_rejects_oversize_and_wrong_type_before_stt(client, monkeypatch):
    from app.config import settings
    from app.main import app

    stt = FakeStt()
    app.state.voice_runtime = FakeRuntime(VoiceService(stt, FakeTts(), AudioStore()))
    monkeypatch.setattr(settings, "voice_max_bytes", 32)
    fields = {"guardian_id": "g-3-1", "student_id": "s-3-1"}
    too_big = client.post(
        "/voice/chat",
        data=fields,
        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")},
    )
    assert too_big.status_code == 413
    monkeypatch.setattr(settings, "voice_max_bytes", 5_242_880)
    wrong_type = client.post(
        "/voice/chat",
        data=fields,
        files={"audio": ("voice.txt", b"not audio", "text/plain")},
    )
    assert wrong_type.status_code == 415
    monkeypatch.setattr(settings, "voice_max_seconds", 0.01)
    too_long = client.post(
        "/voice/chat",
        data=fields,
        files={"audio": ("voice.wav", wav_bytes(0.05), "audio/wav")},
    )
    assert too_long.status_code == 413
    assert stt.calls == 0


def test_speak_and_disabled_status(client):
    from app.main import app

    app.state.voice_runtime = FakeRuntime(VoiceService(FakeStt(), FakeTts(), AudioStore()))
    response = client.post("/voice/speak", json={"text": "Hello", "language": "en"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"

    app.state.voice_runtime.state = "warming"
    assert client.get("/voice/status").json() == {"status": "warming"}
    assert client.post("/voice/speak", json={"text": "Hello", "language": "en"}).status_code == 503

    app.state.voice_runtime.state = "error"
    app.state.voice_runtime.error = "secret startup path"
    assert client.get("/voice/status").json() == {"status": "error"}
    assert client.post("/voice/speak", json={"text": "Hello", "language": "en"}).status_code == 503

    app.state.voice_runtime.state = "disabled"
    assert client.get("/voice/status").status_code == 404
    assert client.post("/voice/speak", json={"text": "Hello", "language": "en"}).status_code == 404
    assert client.post("/voice/chat").status_code == 404
    assert client.get("/voice/audio/missing", params={"guardian_id": "g-3-1"}).status_code == 404


def test_lifespan_warms_in_background_and_cleans_up():
    from app.main import app, lifespan

    class SlowRuntime:
        state = "warming"

        def __init__(self):
            self.started = asyncio.Event()
            self.closed = False

        async def warm(self):
            self.started.set()
            await asyncio.Event().wait()

        def close(self):
            self.closed = True

    old_runtime = getattr(app.state, "voice_runtime", None)
    runtime = SlowRuntime()
    app.state.voice_runtime = runtime

    async def run():
        async with lifespan(app):
            await asyncio.wait_for(runtime.started.wait(), timeout=0.1)
            assert not app.state.voice_warm_task.done()
        assert runtime.closed

    try:
        asyncio.run(run())
    finally:
        app.state.voice_runtime = old_runtime
