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


_FFMPEG_TIMEOUT_SECONDS = 30.0


def convert_upload_to_wav(data: bytes, *, max_seconds: float) -> bytes:
    """Turn a browser or WhatsApp upload into 16 kHz mono PCM for speech-to-text.

    The byte limit is checked by the caller. Duration uses the same cap as a
    WhatsApp voice note, and an over-long upload is rejected before transcription.
    """
    if not data:
        raise AudioValidationError(422, "Audio is empty")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioValidationError(503, "Voice audio conversion is unavailable")
    limit = max(0.0, float(max_seconds))
    with tempfile.TemporaryDirectory(prefix="browser-voice-") as directory:
        source = Path(directory) / "upload"
        target = Path(directory) / "speech.wav"
        source.write_bytes(data)
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-t",
            f"{limit + 0.25:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(target),
        ]
        _run_ffmpeg(command)
        wav = target.read_bytes() if target.is_file() else b""
    if not (len(wav) >= 12 and wav.startswith(b"RIFF") and wav[8:12] == b"WAVE"):
        raise AudioValidationError(415, "Unsupported audio format")
    if _wav_duration(wav) > limit:
        raise AudioValidationError(413, f"Audio exceeds the {limit:g} second limit")
    return wav


def _wav_duration(wav: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav), "rb") as source:
            return source.getnframes() / max(source.getframerate(), 1)
    except (wave.Error, EOFError):
        raise AudioValidationError(422, "Invalid audio") from None


def _run_ffmpeg(command: list[str]) -> None:
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
        raise AudioValidationError(503, "Voice audio conversion is unavailable") from None
    except OSError:
        raise AudioValidationError(503, "Voice audio conversion is unavailable") from None
    if result.returncode != 0:
        raise AudioValidationError(415, "Unsupported audio format")
