"""ffmpeg-based helpers for WhatsApp voice notes.

tests/fixtures/voice_note.ogg is a generated 1-second 440 Hz tone (no speech,
no personal data), made with:
    ffmpeg -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=1" \
        -ac 1 -c:a libopus -b:a 16k -map_metadata -1 tests/fixtures/voice_note.ogg
"""

import io
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app.voice import audio
from app.voice.audio import AudioToolError, InvalidAudioError, audio_duration, to_whisper_wav

VOICE_NOTE = (Path(__file__).parent / "fixtures" / "voice_note.ogg").read_bytes()
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")


def test_fixture_is_a_real_ogg_file():
    assert VOICE_NOTE.startswith(b"OggS")
    assert b"OpusHead" in VOICE_NOTE[:200]


@needs_ffmpeg
def test_ogg_opus_converts_to_16k_mono_16bit_wav():
    wav = to_whisper_wav(VOICE_NOTE)
    with wave.open(io.BytesIO(wav), "rb") as source:
        assert source.getframerate() == 16_000
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getnframes() == pytest.approx(16_000, abs=800)
        assert source.readframes(source.getnframes()).strip(b"\0")


@needs_ffmpeg
def test_conversion_is_capped_by_max_seconds():
    wav = to_whisper_wav(VOICE_NOTE, max_seconds=0.5)
    with wave.open(io.BytesIO(wav), "rb") as source:
        assert source.getnframes() / source.getframerate() == pytest.approx(0.5, abs=0.05)


@needs_ffmpeg
def test_duration_of_real_voice_note():
    assert audio_duration(VOICE_NOTE) == pytest.approx(1.0, abs=0.1)


@needs_ffmpeg
def test_duration_with_limit_stops_decoding_just_past_it():
    duration = audio_duration(VOICE_NOTE, limit=0.25)
    assert 0.25 < duration <= 1.3


@needs_ffmpeg
@pytest.mark.parametrize("data", [b"this is not audio at all", b"OggS" + b"\0" * 64])
def test_non_audio_and_corrupt_bytes_are_rejected(data):
    with pytest.raises(InvalidAudioError):
        audio_duration(data)
    with pytest.raises(InvalidAudioError):
        to_whisper_wav(data)


@pytest.mark.parametrize("func", [audio_duration, to_whisper_wav])
def test_empty_input_is_rejected_without_running_ffmpeg(monkeypatch, func):
    monkeypatch.setattr(audio.subprocess, "run", lambda *a, **k: pytest.fail("ffmpeg must not run"))
    with pytest.raises(InvalidAudioError):
        func(b"")


def test_missing_ffmpeg_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    with pytest.raises(AudioToolError, match="not installed"):
        to_whisper_wav(VOICE_NOTE)


def test_ffmpeg_timeout_is_a_tool_error(monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(audio.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(audio.subprocess, "run", slow)
    with pytest.raises(AudioToolError, match="timed out"):
        audio_duration(VOICE_NOTE)


def test_ffmpeg_failure_and_silent_output_are_invalid_audio(monkeypatch):
    results = iter([
        subprocess.CompletedProcess([], 1, b"", b"Invalid data"),
        subprocess.CompletedProcess([], 0, b"", b""),
    ])
    monkeypatch.setattr(audio.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(audio.subprocess, "run", lambda *a, **k: next(results))
    with pytest.raises(InvalidAudioError):
        to_whisper_wav(VOICE_NOTE)
    with pytest.raises(InvalidAudioError):
        to_whisper_wav(VOICE_NOTE)


def test_ffmpeg_runs_without_a_shell_using_pipes_and_a_timeout(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"\0\0" * 160, b"")

    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio.subprocess, "run", fake_run)
    to_whisper_wav(VOICE_NOTE, max_seconds=60)

    command, kwargs = calls[0]
    assert isinstance(command, list) and command[0] == "/usr/bin/ffmpeg"
    assert "shell" not in kwargs
    assert kwargs["input"] == VOICE_NOTE and kwargs["timeout"] > 0
    assert command[command.index("-i") + 1] == "pipe:0" and command[-1] == "pipe:1"
    assert command[command.index("-t") + 1] == "60"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-ac") + 1] == "1"
