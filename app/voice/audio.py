from __future__ import annotations

import io
import shutil
import subprocess
import wave

WHISPER_SAMPLE_RATE = 16_000
FFMPEG_TIMEOUT_SECONDS = 30.0
# Duration is measured on a cheap low-rate decode; only the length matters.
_DURATION_SAMPLE_RATE = 8_000


class AudioValidationError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class InvalidAudioError(ValueError):
    """The bytes are empty or ffmpeg cannot decode them as audio."""


class AudioToolError(RuntimeError):
    """ffmpeg is missing or did not finish in time; not the sender's fault."""


def to_whisper_wav(data: bytes, *, max_seconds: float | None = None) -> bytes:
    """Decode any ffmpeg-readable audio (e.g. WhatsApp OGG/Opus) to 16 kHz mono
    16-bit PCM WAV. ``max_seconds`` caps how much audio is decoded."""
    pcm = _decode_pcm(data, WHISPER_SAMPLE_RATE, max_seconds)
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(WHISPER_SAMPLE_RATE)
        output.writeframes(pcm)
    return target.getvalue()


def audio_duration(data: bytes, *, limit: float | None = None) -> float:
    """Seconds of audio ffmpeg actually decodes (container metadata is not
    trusted). With ``limit``, decoding stops just past it, so anything longer
    reports a value above ``limit`` without decoding the whole file."""
    cap = limit + 1 if limit is not None else None
    pcm = _decode_pcm(data, _DURATION_SAMPLE_RATE, cap)
    return len(pcm) / (_DURATION_SAMPLE_RATE * 2)


def _decode_pcm(data: bytes, sample_rate: int, max_seconds: float | None) -> bytes:
    if not data:
        raise InvalidAudioError("Audio is empty")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioToolError("ffmpeg is not installed or not on PATH")
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vn"]
    if max_seconds is not None:
        command += ["-t", f"{max_seconds:g}"]
    command += ["-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    try:
        result = subprocess.run(
            command, input=data, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired:
        raise AudioToolError("ffmpeg timed out while decoding audio") from None
    except OSError as exc:
        raise AudioToolError(f"ffmpeg could not be started: {exc.strerror or exc}") from None
    if result.returncode != 0:
        raise InvalidAudioError("ffmpeg could not decode the audio")
    if not result.stdout:
        raise InvalidAudioError("Audio contains no decodable sound")
    return result.stdout


SUPPORTED_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "video/mp4",
    "audio/x-m4a",
    "audio/m4a",
    "audio/ogg",
    "audio/webm",
}


def validate_audio(
    audio: bytes, content_type: str | None, *, max_bytes: int, max_seconds: float
) -> None:
    if len(audio) > max_bytes:
        raise AudioValidationError(413, f"Audio exceeds the {max_bytes} byte limit")
    if not audio:
        raise AudioValidationError(422, "Audio is empty")
    media_type = (content_type or "").split(";", 1)[0].lower()
    if media_type not in SUPPORTED_TYPES:
        raise AudioValidationError(415, "Unsupported audio format")
    if not _matches_signature(audio, media_type):
        raise AudioValidationError(415, "Audio content does not match its format")
    duration = _duration_seconds(audio, media_type)
    if duration > max_seconds:
        raise AudioValidationError(413, f"Audio exceeds the {max_seconds:g} second limit")


def _matches_signature(audio: bytes, media_type: str) -> bool:
    if media_type in {"audio/wav", "audio/x-wav"}:
        return len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"
    if media_type in {"audio/mpeg", "audio/mp3"}:
        return audio.startswith(b"ID3") or (len(audio) > 1 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0)
    if media_type == "audio/ogg":
        return audio.startswith(b"OggS")
    if media_type == "audio/webm":
        return audio.startswith(b"\x1a\x45\xdf\xa3")
    if media_type in {"audio/mp4", "video/mp4", "audio/x-m4a", "audio/m4a"}:
        return len(audio) >= 12 and audio[4:8] == b"ftyp"
    return False


def _duration_seconds(audio: bytes, media_type: str) -> float:
    if media_type in {"audio/wav", "audio/x-wav"}:
        try:
            with wave.open(io.BytesIO(audio), "rb") as source:
                return source.getnframes() / max(source.getframerate(), 1)
        except (wave.Error, EOFError):
            raise AudioValidationError(422, "Invalid WAV audio") from None
    try:
        import av

        with av.open(io.BytesIO(audio), mode="r") as container:
            if container.duration is not None:
                return float(container.duration / av.time_base)
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise AudioValidationError(422, "Audio stream is missing")
            if stream.duration is not None and stream.time_base is not None:
                return float(stream.duration * stream.time_base)
            duration = 0.0
            for frame in container.decode(stream):
                duration += float(frame.samples / frame.sample_rate)
            return duration
    except AudioValidationError:
        raise
    except Exception:
        raise AudioValidationError(422, "Invalid audio") from None
