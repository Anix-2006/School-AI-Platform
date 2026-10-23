"""Time the in-memory upload conversion.

``app`` lives at the repository root. This script adds that root to the
import path, same as ``scripts/benchmark_tts_sentences.py``, so it can be
run from the repo root:

    py -3.11 scripts/benchmark_ffmpeg_convert.py

The figure is the convert stage only: one second of audio to 16 kHz WAV.
ffmpeg runs through pipes, so the run does not write that audio to disk.
"""

from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENAI_API_KEY", "benchmark-only")

from app.voice.audio import to_whisper_wav


def _one_second_webm() -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is required")
    result = subprocess.run(
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
            "1.0",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise SystemExit("could not build the sample audio")
    return result.stdout


def main() -> None:
    webm = _one_second_webm()
    samples = []
    wav = b""
    for _ in range(5):
        started = time.perf_counter()
        wav = to_whisper_wav(webm, max_seconds=30)
        samples.append(time.perf_counter() - started)
    print(f"webm_bytes={len(webm)}")
    print(f"wav_bytes={len(wav)}")
    print(f"convert_median_s={statistics.median(samples):.4f}")
    print("runs_s=" + " ".join(f"{sample:.4f}" for sample in samples))


if __name__ == "__main__":
    main()
