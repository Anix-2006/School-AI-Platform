"""STT/TTS interface contracts (issue #36)."""

from __future__ import annotations

import io
import os
import wave
from pathlib import Path

import pytest

from app.voice.stt import SpeechToText, Transcript, WhisperSTT, get_stt, reset_stt_for_tests
from app.voice.tts import GTTSSpeaker, TextToSpeech, get_tts, reset_tts_for_tests


class FakeSpeechToText:
    def load(self) -> None:
        return None

    def transcribe(self, audio_wav: bytes, language: str | None = None) -> Transcript:
        return Transcript(
            text="hello",
            language=language or "en",
            duration_seconds=0.5,
            confidence=0.9,
        )


class FakeTextToSpeech:
    def speak(self, text: str, language: str) -> bytes:
        return f"ID3:{language}:{text}".encode()


def _silent_wav(seconds: float = 0.2, rate: int = 16000) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * int(rate * seconds))
    return target.getvalue()


def test_fake_stt_matches_speech_to_text_protocol():
    stt: SpeechToText = FakeSpeechToText()
    result = stt.transcribe(_silent_wav(), "en")
    assert isinstance(result, Transcript)
    assert result.text == "hello"
    assert result.language == "en"
    assert result.duration_seconds == 0.5


def test_fake_tts_matches_text_to_speech_protocol():
    tts: TextToSpeech = FakeTextToSpeech()
    audio = tts.speak("Hello", "en")
    assert isinstance(audio, bytes)
    assert audio.startswith(b"ID3:")


def test_gtts_speaker_rejects_unsupported_language():
    speaker = GTTSSpeaker(cache_size=2)
    with pytest.raises(ValueError, match="Unsupported voice language"):
        speaker.speak("Hello", "xx")


def test_gtts_language_map_covers_en_hi_te():
    assert GTTSSpeaker.SUPPORTED_LANGUAGES == frozenset({"en", "hi", "te"})


def test_get_stt_and_get_tts_return_singletons():
    reset_stt_for_tests()
    reset_tts_for_tests()
    try:
        assert get_stt() is get_stt()
        assert get_tts() is get_tts()
        assert isinstance(get_stt(), WhisperSTT)
        assert isinstance(get_tts(), GTTSSpeaker)
    finally:
        reset_stt_for_tests()
        reset_tts_for_tests()


def test_no_direct_engine_imports_outside_voice_modules():
    """Agents, routes and webhooks must not import faster_whisper or gtts."""
    root = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root.parent).as_posix()
        if relative.startswith("app/voice/"):
            continue
        text = path.read_text(encoding="utf-8")
        if "faster_whisper" in text or "from gtts" in text or "import gtts" in text:
            offenders.append(relative)
    assert offenders == []


def _spoken_hello_audio() -> bytes:
    """Build short spoken audio via gTTS (integration only)."""
    from gtts import gTTS

    buf = io.BytesIO()
    gTTS("Hello", lang="en", tld="co.in").write_to_fp(buf)
    return buf.getvalue()


@pytest.mark.skipif(
    os.getenv("RUN_VOICE_INTEGRATION") != "1",
    reason="Set RUN_VOICE_INTEGRATION=1 to run the real Whisper/gTTS checks",
)
def test_whisper_transcribes_short_english_audio():
    """Slow: needs the Whisper model and gTTS network access."""
    stt = WhisperSTT()
    stt.load()
    # faster-whisper accepts the MP3 bytes through its decoder; the interface
    # is named for WAV but the first implementation is format-tolerant.
    result = stt.transcribe(_spoken_hello_audio(), "en")
    assert isinstance(result, Transcript)
    assert result.text.strip()
    assert "hello" in result.text.casefold()


@pytest.mark.skipif(
    os.getenv("RUN_VOICE_INTEGRATION") != "1",
    reason="Set RUN_VOICE_INTEGRATION=1 to run the real Whisper/gTTS checks",
)
@pytest.mark.parametrize("language", ["en", "hi", "te"])
def test_gtts_speak_returns_mp3_for_hello(language):
    audio = GTTSSpeaker(cache_size=4).speak("Hello", language)
    assert isinstance(audio, bytes)
    assert len(audio) > 32
    # gTTS MP3 payloads typically start with an ID3 tag or an MPEG frame sync.
    assert audio[:3] == b"ID3" or audio[0] == 0xFF
