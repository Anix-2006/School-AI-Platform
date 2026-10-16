"""Baseline clip discovery for the voice load tester."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CLIPS_DIR = REPO_ROOT / "tests" / "fixtures" / "voice_load"

_AUDIO_SUFFIXES = {".wav", ".webm", ".ogg", ".mp3", ".m4a", ".mpeg"}

_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".m4a": "audio/mp4",
}


def content_type_for(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def discover_clips(clips_dir: Path | None = None) -> list[Path]:
    root = Path(clips_dir) if clips_dir is not None else DEFAULT_CLIPS_DIR
    if not root.is_dir():
        raise FileNotFoundError(
            f"Clips directory not found: {root}. "
            "Generate synthetic fixtures under tests/fixtures/voice_load "
            "or pass --clips-dir."
        )
    clips = sorted(
        p
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_SUFFIXES
    )
    if not clips:
        raise FileNotFoundError(f"No audio clips found in {root}")
    return clips


def cycle_clips(clips: list[Path], count: int) -> list[Path]:
    if count < 1:
        raise ValueError("need at least one clip assignment")
    return [clips[i % len(clips)] for i in range(count)]
