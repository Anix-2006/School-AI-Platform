"""In-memory ffmpeg conversions leave no audio files behind (issue #53)."""

import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest

from app.voice.audio import (
    AudioValidationError,
    convert_upload_to_wav,
    mp3_to_ogg_opus,
    to_whisper_wav,
)
from app.voice.tts import concat_mp3


def _ffmpeg(*args: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is required")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", *args, "pipe:1"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        pytest.fail(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def _webm() -> bytes:
    return _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=16000:cl=mono",
        "-t",
        "0.3",
        "-c:a",
        "libopus",
        "-f",
        "webm",
    )


def _mp3(frequency: int = 440) -> bytes:
    return _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration=0.35",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "7",
        "-f",
        "mp3",
    )


def _forbid_temp_files(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("voice conversion must not create a temporary file")

    monkeypatch.setattr(tempfile, "mkdtemp", boom)
    monkeypatch.setattr(tempfile, "mkstemp", boom)


def test_whisper_wav_and_upload_conversion_return_bytes(monkeypatch):
    _forbid_temp_files(monkeypatch)
    webm = _webm()
    wav = to_whisper_wav(webm, max_seconds=5)
    assert convert_upload_to_wav(webm, max_seconds=5) == wav
    assert wav.startswith(b"RIFF") and wav[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getframerate() == 16000
        assert source.getsampwidth() == 2
        assert source.getnframes() > 0


def test_mp3_to_ogg_opus_returns_ogg_bytes(monkeypatch):
    _forbid_temp_files(monkeypatch)
    ogg = mp3_to_ogg_opus(_mp3())
    assert ogg.startswith(b"OggS")
    assert len(ogg) > 32


def test_joined_mp3_is_bytes_and_keeps_both_clips(monkeypatch):
    import av

    _forbid_temp_files(monkeypatch)
    joined = concat_mp3([_mp3(440), _mp3(880)])
    assert joined[:3] == b"ID3" or joined[0] == 0xFF
    with av.open(io.BytesIO(joined), mode="r") as container:
        stream = next(item for item in container.streams if item.type == "audio")
        duration = 0.0
        for frame in container.decode(stream):
            duration += float(frame.samples / frame.sample_rate)
    assert duration > 0.6


def test_conversion_timeout_is_a_clear_error(monkeypatch):
    def expire(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", expire)
    with pytest.raises(AudioValidationError, match="timed out") as caught:
        to_whisper_wav(b"not-audio", max_seconds=5)
    assert caught.value.status_code == 503


def test_voice_turn_creates_no_temp_audio(client, monkeypatch, tmp_path):
    from app.main import app
    from app.services import chat_service
    from app.voice.service import VoiceService
    from app.voice.stt import Transcript
    from app.voice.storage import AudioStore
    from tests.fakes import FakeGraph

    class Hearing:
        def transcribe(self, audio_wav, language=None, initial_prompt=None):
            assert audio_wav.startswith(b"RIFF")
            return Transcript("Any homework?", "en", duration_seconds=0.3, confidence=1.0)

    class Speaking:
        SUPPORTED_LANGUAGES = {"en", "hi", "te"}

        def speak(self, text, language):
            return _mp3()

    class Ready:
        state = "ready"

        def __init__(self, service):
            self.service = service

    monkeypatch.setattr(chat_service, "orchestrator_graph", FakeGraph())
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp
    real_mkstemp = tempfile.mkstemp

    def track_dir(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    def track_file(*args, **kwargs):
        handle = real_mkstemp(*args, **kwargs)
        created.append(handle[1])
        return handle

    monkeypatch.setattr(tempfile, "mkdtemp", track_dir)
    monkeypatch.setattr(tempfile, "mkstemp", track_file)
    previous = getattr(app.state, "voice_runtime", None)
    app.state.voice_runtime = Ready(VoiceService(Hearing(), Speaking(), AudioStore()))
    try:
        response = client.post(
            "/voice",
            data={"guardian_id": "g-3-1", "session_id": "memory"},
            files={"audio": ("question.webm", _webm(), "audio/webm")},
        )
    finally:
        app.state.voice_runtime = previous
    assert response.status_code == 200
    assert response.json()["transcript"] == "Any homework?"
    names = [Path(path).name for path in created]
    assert not any(name.startswith(("browser-voice-", "tts-join-")) for name in names)
    leftover = [
        path.name
        for path in tmp_path.rglob("*")
        if path.suffix.lower() in {".wav", ".mp3", ".ogg", ".webm", ".opus", ".m4a"}
        or path.name.startswith(("browser-voice-", "tts-join-"))
    ]
    assert leftover == []
