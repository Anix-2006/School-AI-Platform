from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


class AudioValidationError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class AudioConversionError(RuntimeError):
    """ffmpeg could not turn a reply MP3 into an OGG/Opus voice note."""


# Mono Opus at a voice bitrate. WhatsApp shows this as a voice note.
# 16 kHz is the input rate stored in OpusHead. Opus itself decodes at 48 kHz.
_VOICE_SAMPLE_RATE = 16_000
_VOICE_BITRATE = "24k"
_FFMPEG_TIMEOUT_SECONDS = 30.0


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


def mp3_to_ogg_opus(mp3: bytes) -> bytes:
    """Convert gTTS MP3 bytes to mono OGG/Opus at a voice bitrate.

    The Ogg muxer needs a real file, so ffmpeg writes into a temporary
    directory that is removed before this returns. The caller drops the
    returned bytes after the WhatsApp send.
    """
    if not mp3:
        raise AudioConversionError("Audio is empty")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioConversionError("ffmpeg is not installed or not on PATH")
    with tempfile.TemporaryDirectory(prefix="wa-voice-") as directory:
        source = Path(directory) / "reply.mp3"
        target = Path(directory) / "reply.ogg"
        source.write_bytes(mp3)
        _run_ffmpeg(_opus_command(ffmpeg, str(source), str(target)))
        data = target.read_bytes() if target.is_file() else b""
    if not _is_ogg_opus(data):
        raise AudioConversionError("ffmpeg output is not an OGG/Opus voice note")
    return data


def _opus_command(ffmpeg: str, source: str, target: str) -> list[str]:
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source,
        "-map_metadata",
        "-1",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(_VOICE_SAMPLE_RATE),
        "-c:a",
        "libopus",
        "-b:a",
        _VOICE_BITRATE,
        "-application",
        "voip",
        "-f",
        "ogg",
        target,
    ]


def _is_ogg_opus(data: bytes) -> bool:
    return data.startswith(b"OggS") and b"OpusHead" in data


def _run_ffmpeg(command: list[str]) -> bytes:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT_SECONDS,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise AudioConversionError("ffmpeg timed out while converting audio") from None
    except OSError as exc:
        raise AudioConversionError(f"ffmpeg could not be started: {exc.strerror or exc}") from None
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise AudioConversionError(detail[:240] or "ffmpeg could not convert MP3 to OGG/Opus")
    return result.stdout
