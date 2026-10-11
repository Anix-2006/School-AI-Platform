from __future__ import annotations

import io
import wave


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
