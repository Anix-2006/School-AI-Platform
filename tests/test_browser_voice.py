import base64
import io
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import pytest

from app.config import settings
from app.voice.service import VoiceService
from app.voice.stt import Transcript
from app.voice.storage import AudioStore


class FakeStt:
    def __init__(self, text="When is Annual Day?", language="en"):
        self.result = Transcript(text, language, 1.0, 0.2)
        self.calls = 0
        self.audio = []

    def load(self):
        return None

    def transcribe(self, audio, content_type, language=None, initial_prompt=None):
        self.calls += 1
        self.audio.append((audio, content_type))
        return self.result


class FakeTts:
    SUPPORTED_LANGUAGES = {"en", "hi", "te"}

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, language):
        self.calls.append((text, language))
        return b"ID3speech"


class FakeRuntime:
    state = "ready"

    def __init__(self, service):
        self.service = service

    def status(self):
        return {"status": self.state}


def _wav_bytes(seconds=0.2, rate=8000):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * int(rate * seconds))
    return target.getvalue()


def _webm_bytes():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is required to convert the browser voice upload")
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "question.webm"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=16000:cl=mono",
                "-t",
                "0.2",
                "-c:a",
                "libopus",
                str(target),
            ],
            check=True,
            capture_output=True,
        )
        return target.read_bytes()


@pytest.fixture
def browser_voice(client, monkeypatch):
    from app.main import app
    from app.services import chat_service
    from tests.fakes import FakeGraph

    graph = FakeGraph()
    monkeypatch.setattr(chat_service, "orchestrator_graph", graph)
    stt = FakeStt()
    tts = FakeTts()
    previous = getattr(app.state, "voice_runtime", None)
    app.state.voice_runtime = FakeRuntime(VoiceService(stt, tts, AudioStore()))
    try:
        yield stt, tts, graph
    finally:
        app.state.voice_runtime = previous


def test_browser_voice_answers_and_returns_spoken_audio(client, browser_voice, store):
    stt, tts, graph = browser_voice
    guardian = next(item for item in store.find("guardians") if item["id"] == "g-3-1")
    response = client.post(
        "/voice",
        data={"guardian_id": "g-3-1", "session_id": "demo-1"},
        files={"audio": ("question.webm", _webm_bytes(), "audio/webm")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "When is Annual Day?"
    assert body["reply"] == "Here is your update."
    assert body["agent_used"] == "daily_update"
    assert body["language"] == guardian["preferred_language"] == "hi"
    assert base64.b64decode(body["audio_base64"]) == b"ID3speech"
    assert set(body) == {"transcript", "reply", "agent_used", "language", "audio_base64"}
    assert stt.calls == 1
    audio, content_type = stt.audio[0]
    assert content_type == "audio/wav"
    with wave.open(io.BytesIO(audio), "rb") as decoded:
        assert decoded.getnchannels() == 1
        assert decoded.getframerate() == 16000
    assert graph.calls[0]["state"]["student_id"] == guardian["student_id"]
    assert graph.calls[0]["state"]["language"] == "hi"
    assert graph.calls[0]["state"]["channel"] == "voice"
    assert tts.calls[0][1] == "hi"


def test_browser_voice_refuses_a_parent_without_consent(client, browser_voice):
    stt, tts, graph = browser_voice
    response = client.post(
        "/voice",
        data={"guardian_id": "g-7-1"},
        files={"audio": ("question.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()
    assert stt.calls == 0
    assert tts.calls == []
    assert graph.calls == []


def test_browser_voice_unknown_guardian_is_not_transcribed(client, browser_voice):
    stt, _tts, _graph = browser_voice
    response = client.post(
        "/voice",
        data={"guardian_id": "g-nobody"},
        files={"audio": ("question.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 404
    assert stt.calls == 0


def test_browser_voice_rejects_audio_over_the_size_limit(client, browser_voice, monkeypatch):
    stt, _tts, _graph = browser_voice
    monkeypatch.setattr(settings, "voice_max_bytes", 32)

    def converted(*_args, **_kwargs):
        raise AssertionError("oversized audio was converted")

    monkeypatch.setattr("app.api.routes_voice.convert_upload_to_wav", converted)
    response = client.post(
        "/voice",
        data={"guardian_id": "g-3-1"},
        files={"audio": ("question.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 413
    assert stt.calls == 0


def test_browser_voice_rejects_audio_over_the_length_limit(client, browser_voice, monkeypatch):
    stt, _tts, _graph = browser_voice
    monkeypatch.setattr(settings, "voice_max_seconds", 0.05)
    started = time.perf_counter()
    response = client.post(
        "/voice",
        data={"guardian_id": "g-3-1"},
        files={"audio": ("question.wav", _wav_bytes(seconds=1.0), "audio/wav")},
    )
    assert response.status_code == 413
    assert stt.calls == 0
    assert time.perf_counter() - started < 10


def test_browser_voice_does_not_run_when_voice_is_disabled(client, browser_voice):
    stt, tts, graph = browser_voice
    from app.main import app

    app.state.voice_runtime.state = "disabled"
    response = client.post(
        "/voice",
        data={"guardian_id": "g-3-1"},
        files={"audio": ("question.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 404
    assert stt.calls == 0
    assert tts.calls == []
    assert graph.calls == []


@pytest.mark.parametrize("language", ["en", "hi", "te"])
def test_browser_voice_speaks_the_guardian_language(client, browser_voice, monkeypatch, language):
    from app.services import chat_service
    import app.api.routes_voice as routes

    stt, tts, graph = browser_voice

    def resolve(tenant_id, guardian_id, student_id=None):
        guardian, student = chat_service.resolve_parent(tenant_id, guardian_id, student_id)
        return {**guardian, "preferred_language": language}, student

    monkeypatch.setattr(routes, "resolve_parent", resolve)
    response = client.post(
        "/voice",
        data={"guardian_id": "g-3-1", "session_id": f"lang-{language}"},
        files={"audio": ("question.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == language
    assert tts.calls[-1][1] == language
    assert graph.calls[-1]["state"]["language"] == language
    assert stt.calls == 1


def test_browser_voice_does_not_claim_an_unsupported_language(client, browser_voice, monkeypatch):
    from app.services import chat_service
    import app.api.routes_voice as routes

    _stt, tts, graph = browser_voice
    tts.SUPPORTED_LANGUAGES = {"en", "hi"}

    def resolve(tenant_id, guardian_id, student_id=None):
        guardian, student = chat_service.resolve_parent(tenant_id, guardian_id, student_id)
        return {**guardian, "preferred_language": "te"}, student

    monkeypatch.setattr(routes, "resolve_parent", resolve)
    response = client.post(
        "/voice",
        data={"guardian_id": "g-3-1"},
        files={"audio": ("question.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["language"] == "en"
    assert tts.calls[-1][1] == "en"
    assert graph.calls[-1]["state"]["language"] == "en"


def test_voice_page_shows_the_default_duration_limit(client, monkeypatch):
    from app.config import Settings

    default = Settings.model_fields["voice_max_seconds"].default
    assert default == 30
    monkeypatch.setattr(settings, "voice_max_seconds", default)
    page = client.get("/flows/voice")
    assert page.status_code == 200
    assert 'data-max-seconds="30"' in page.text
    assert "__VOICE_MAX_SECONDS__" not in page.text
    assert "const MAX_MS = 30000" not in page.text
    assert "EARLY_STOP_SECONDS" in page.text


def test_voice_page_shows_the_configured_duration_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_max_seconds", 12)
    page = client.get("/flows/voice")
    assert 'data-max-seconds="12"' in page.text
    assert "__VOICE_MAX_SECONDS__" not in page.text


def test_browser_voice_accepts_audio_within_the_default_limit(client, browser_voice, monkeypatch):
    stt, _tts, _graph = browser_voice
    monkeypatch.setattr(settings, "voice_max_seconds", 30)
    response = client.post(
        "/voice",
        data={"guardian_id": "g-3-1"},
        files={"audio": ("question.wav", _wav_bytes(seconds=1.0), "audio/wav")},
    )
    assert response.status_code == 200
    assert stt.calls == 1


def test_voice_demo_page_is_served(client):
    page = client.get("/flows/voice")
    assert page.status_code == 200
    assert "Heard:" in page.text
    assert "MediaRecorder" in page.text
    assert 'id="record"' in page.text
    nav = client.get("/nav.js")
    assert nav.status_code == 200
    assert "/flows/voice" in nav.text
    assert "Voice demo" in nav.text
