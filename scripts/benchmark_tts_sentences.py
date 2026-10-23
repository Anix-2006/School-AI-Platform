"""Compare one gTTS request with parallel sentence requests.

``app`` lives at the repository root, not in a backend subdirectory. This
script adds that root to the import path the same way
``scripts/export_parent_teacher_turns.py`` does, so it can be run from the
repo root without setting PYTHONPATH:

    py -3.11 scripts/benchmark_tts_sentences.py

The timings are real gTTS network calls, not mocks. The last figure is a
second pass of the same sentences, served from the sentence cache.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENAI_API_KEY", "benchmark-only")

from app.voice.tts import GTTSSpeaker, split_sentences

TEXT = (
    "School is closed tomorrow because of the holiday. "
    "Please bring the homework sheet on Friday. "
    "The bus will leave at four."
)


def main() -> None:
    sentences = split_sentences(TEXT, "en")
    print(f"sentences={len(sentences)}")
    for sentence in sentences:
        print(f"  - {sentence}")
    speaker = GTTSSpeaker(cache_size=8)

    speaker._cache.clear()
    started = time.perf_counter()
    speaker._render(TEXT, "en")
    single = time.perf_counter() - started

    speaker._cache.clear()
    started = time.perf_counter()
    for sentence in sentences:
        speaker._render(sentence, "en")
    serial = time.perf_counter() - started

    speaker._cache.clear()
    started = time.perf_counter()
    speaker.speak(TEXT, "en")
    parallel = time.perf_counter() - started

    started = time.perf_counter()
    speaker.speak(TEXT, "en")
    cached = time.perf_counter() - started

    print(f"one_request_s={single:.3f}")
    print(f"serial_sentences_s={serial:.3f}")
    print(f"parallel_sentences_s={parallel:.3f}")
    print(f"cached_sentences_s={cached:.3f}")


if __name__ == "__main__":
    main()
