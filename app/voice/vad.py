"""Cut a live microphone stream into utterances.

WebRTC VAD was chosen over Silero. On spoken English, Hindi, and Telugu
questions with added background noise, aggressiveness modes 0–3 each split
the recording into the same three utterances, and the detector is a small
native library rather than a neural model. The installable package is
``webrtcvad-wheels``; it provides the ``webrtcvad`` module without a local
C++ build.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import webrtcvad

from app.config import settings

_WEBRTC_RATES = {8000, 16000, 32000, 48000}
_WEBRTC_FRAMES_MS = {10, 20, 30}


@dataclass(frozen=True)
class VadEvents:
    utterances: list[bytes] = field(default_factory=list)
    speech_started: bool = False


class Pcm16Resampler:
    """Stream PCM16 mono from the browser rate to the VAD rate."""

    def __init__(self, source_rate: int, target_rate: int):
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("sample rate must be positive")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._carry = np.empty(0, dtype=np.float32)
        self._next_source_pos = 0.0

    def push(self, pcm: bytes) -> bytes:
        if len(pcm) < 2:
            return b""
        if len(pcm) % 2:
            pcm = pcm[:-1]
        if self.source_rate == self.target_rate:
            return pcm
        incoming = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        audio = np.concatenate((self._carry, incoming))
        if len(audio) < 2:
            self._carry = audio
            return b""
        step = self.source_rate / self.target_rate
        positions: list[float] = []
        pos = self._next_source_pos
        limit = len(audio) - 1
        while pos < limit:
            positions.append(pos)
            pos += step
        self._next_source_pos = pos - (len(audio) - 1)
        self._carry = audio[-1:]
        if not positions:
            return b""
        index = np.asarray(positions, dtype=np.float64)
        left = np.floor(index).astype(np.int32)
        fraction = (index - left).astype(np.float32)
        samples = audio[left] * (1.0 - fraction) + audio[left + 1] * fraction
        return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()


class UtteranceSegmenter:
    """Turn PCM16 mono into one buffer per finished utterance."""

    def __init__(
        self,
        *,
        sample_rate: int,
        frame_ms: int,
        silence_ms: int,
        max_utterance_ms: int,
        min_speech_ms: int,
        sensitivity: int,
        preroll_ms: int,
    ):
        if sample_rate not in _WEBRTC_RATES:
            raise ValueError(f"sample_rate must be one of {sorted(_WEBRTC_RATES)}")
        if frame_ms not in _WEBRTC_FRAMES_MS:
            raise ValueError("frame_ms must be 10, 20, or 30")
        if not 0 <= sensitivity <= 3:
            raise ValueError("sensitivity must be from 0 (most sensitive) to 3")
        if silence_ms <= 0 or max_utterance_ms <= 0 or min_speech_ms <= 0:
            raise ValueError("speech timing settings must be positive")
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.silence_ms = silence_ms
        self.max_utterance_ms = max_utterance_ms
        self.min_speech_ms = min_speech_ms
        self.sensitivity = sensitivity
        self._vad = webrtcvad.Vad(sensitivity)
        self._frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        preroll_frames = max(1, round(preroll_ms / frame_ms)) if preroll_ms > 0 else 0
        self._preroll: deque[bytes] = deque(maxlen=preroll_frames if preroll_frames else 1)
        self._keep_preroll = preroll_frames > 0
        self._pending = bytearray()
        self._in_speech = False
        self._announced = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._current = bytearray()
        self._continued = False

    def push(self, pcm: bytes) -> VadEvents:
        self._pending.extend(pcm)
        utterances: list[bytes] = []
        started = False
        while len(self._pending) >= self._frame_bytes:
            frame = bytes(self._pending[: self._frame_bytes])
            del self._pending[: self._frame_bytes]
            speech = self._vad.is_speech(frame, self.sample_rate)
            frame_started, finished = self._feed(frame, speech)
            started = started or frame_started
            if finished:
                utterances.append(finished)
        return VadEvents(utterances=utterances, speech_started=started)

    def _feed(self, frame: bytes, speech: bool) -> tuple[bool, bytes | None]:
        if not self._in_speech:
            if self._keep_preroll:
                self._preroll.append(frame)
            if not speech:
                return False, None
            self._in_speech = True
            self._silence_ms = 0
            self._speech_ms = self.frame_ms
            self._current = bytearray().join(self._preroll) if self._keep_preroll else bytearray(frame)
            self._preroll.clear()
            return self._mark_started(), self._cut_if_too_long()

        self._current.extend(frame)
        if speech:
            self._speech_ms += self.frame_ms
            self._silence_ms = 0
            started = self._mark_started()
            return started, self._cut_if_too_long()

        self._silence_ms += self.frame_ms
        if self._silence_ms < self.silence_ms:
            return False, None
        return False, self._close()

    def _mark_started(self) -> bool:
        if self._speech_ms < self.min_speech_ms:
            return False
        if self._continued:
            self._continued = False
            self._announced = True
            return False
        if self._announced:
            return False
        self._announced = True
        return True

    def _cut_if_too_long(self) -> bytes | None:
        if self._speech_ms < self.max_utterance_ms:
            return None
        # The speaker has not paused, so the next piece is not an interruption.
        self._continued = True
        return self._close()

    def _close(self) -> bytes | None:
        speech_ms = self._speech_ms
        audio = bytes(self._current)
        self._in_speech = False
        self._announced = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._current = bytearray()
        if speech_ms < self.min_speech_ms or not audio:
            return None
        return audio


def segmenter_from_settings() -> UtteranceSegmenter:
    """Build the live segmenter from the process settings."""
    return UtteranceSegmenter(
        sample_rate=settings.voice_vad_sample_rate,
        frame_ms=settings.voice_vad_frame_ms,
        silence_ms=settings.voice_vad_silence_ms,
        max_utterance_ms=max(1, int(settings.voice_max_seconds * 1000)),
        min_speech_ms=settings.voice_vad_min_speech_ms,
        sensitivity=settings.voice_vad_sensitivity,
        preroll_ms=settings.voice_vad_preroll_ms,
    )
