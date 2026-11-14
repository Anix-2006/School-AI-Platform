"""Download the configured faster-whisper model once, before enabling voice.

Run from the repository root (or /app inside Docker):

    python -m app.voice.download_model

It reads WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE and
WHISPER_MODEL_DIR from the same settings as the app. Files go into the
Hugging Face cache layout under WHISPER_MODEL_DIR. Running it again reuses
files already there; the app itself only loads from that directory and never
downloads.
"""

from __future__ import annotations

import sys

from app.config import settings
from app.services.excel_store import resolve_path


def main() -> int:
    model_dir = resolve_path(settings.whisper_model_dir)
    model = settings.whisper_model
    print(
        f"Preparing Whisper model '{model}' "
        f"(device={settings.whisper_device}, compute_type={settings.whisper_compute_type}) "
        f"in {model_dir} ..."
    )
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "faster-whisper is not installed in this Python environment. "
            "Activate your virtual environment and run: "
            "python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        WhisperModel(
            model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            download_root=str(model_dir),
        )
    except Exception as exc:  # noqa: BLE001 - report any download/load failure and exit nonzero
        print(
            f"Could not download or load Whisper model '{model}' into {model_dir}: "
            f"{type(exc).__name__}: {exc}\n"
            "Check the internet connection, free disk space, write access to "
            "WHISPER_MODEL_DIR, and that WHISPER_MODEL, WHISPER_DEVICE and "
            "WHISPER_COMPUTE_TYPE are valid for this machine.",
            file=sys.stderr,
        )
        return 1
    print(f"Whisper model '{model}' is ready in {model_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
