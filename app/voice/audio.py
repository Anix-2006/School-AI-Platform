from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import wave


logger = logging.getLogger(__name__)

_FFMPEG_TIMEOUT_SECONDS = 30.0


class FfmpegError(RuntimeError):
    """ffmpeg was missing, too slow, or could not convert the bytes."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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


def convert_upload_to_wav(data: bytes, *, max_seconds: float) -> bytes:
    """Turn a browser or WhatsApp upload into 16 kHz mono PCM for speech-to-text."""
    return to_whisper_wav(data, max_seconds=max_seconds)


def to_whisper_wav(data: bytes, *, max_seconds: float) -> bytes:
    """Convert upload bytes to 16 kHz mono WAV without writing a file.

    The byte limit is checked by the caller. Duration uses the same cap as a
    WhatsApp voice note, and an over-long upload is rejected before transcription.
    """
    if not data:
        raise AudioValidationError(422, "Audio is empty")
    limit = max(0.0, float(max_seconds))
    try:
        # Raw PCM avoids the unfinished size fields ffmpeg leaves in a piped WAV.
        pcm = run_ffmpeg_pipe(
            data,
            output_args=[
                "-t",
                f"{limit + 0.25:.3f}",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
            ],
        )
    except FfmpegError as exc:
        raise _validation_error(exc) from None
    if len(pcm) < 2:
        raise AudioValidationError(415, "Unsupported audio format")
    wav = _pcm_to_wav(pcm)
    if _wav_duration(wav) > limit:
        raise AudioValidationError(413, f"Audio exceeds the {limit:g} second limit")
    return wav


def mp3_to_ogg_opus(mp3: bytes) -> bytes:
    """Convert speech MP3 bytes to OGG/Opus bytes. Nothing is written to disk."""
    if not mp3:
        raise AudioValidationError(422, "Audio is empty")
    try:
        ogg = run_ffmpeg_pipe(
            mp3,
            output_args=["-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg"],
        )
    except FfmpegError as exc:
        raise _validation_error(exc) from None
    if not ogg.startswith(b"OggS"):
        raise AudioValidationError(415, "Could not convert speech to Opus")
    return ogg


def run_ffmpeg_pipe(
    data: bytes,
    *,
    input_args: list[str] | None = None,
    output_args: list[str],
    timeout: float = _FFMPEG_TIMEOUT_SECONDS,
) -> bytes:
    """Run ffmpeg from stdin bytes to stdout bytes.

    ``input_args`` are options that must come before ``-i pipe:0``, such as
    the sample rate of raw PCM. ``output_args`` come after the input.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FfmpegError("missing")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        *(input_args or []),
        "-i",
        "pipe:0",
        *output_args,
        "pipe:1",
    ]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            command,
            input=data,
            capture_output=True,
            timeout=timeout,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise FfmpegError("timeout") from None
    except OSError:
        raise FfmpegError("missing") from None
    if result.returncode != 0 or not result.stdout:
        detail = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        if detail:
            logger.warning("ffmpeg failed: %s", detail[:300])
        raise FfmpegError("failed")
    return result.stdout


def _validation_error(exc: FfmpegError) -> AudioValidationError:
    if exc.reason == "timeout":
        return AudioValidationError(503, "Audio conversion timed out")
    if exc.reason == "missing":
        return AudioValidationError(503, "Voice audio conversion is unavailable")
    return AudioValidationError(415, "Unsupported audio format")


def _pcm_to_wav(pcm: bytes, rate: int = 16000) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)
    return target.getvalue()


def _wav_duration(wav: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav), "rb") as source:
            return source.getnframes() / max(source.getframerate(), 1)
    except (wave.Error, EOFError):
        raise AudioValidationError(422, "Invalid audio") from None
