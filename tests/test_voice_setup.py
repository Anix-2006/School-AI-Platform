import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

from app.config import Settings, settings

ROOT = Path(__file__).resolve().parent.parent
WHISPER_ENV = ("WHISPER_MODEL", "WHISPER_DEVICE", "WHISPER_COMPUTE_TYPE", "WHISPER_MODEL_DIR")


def fake_faster_whisper(monkeypatch, error: Exception | None = None):
    calls = []

    class FakeWhisperModel:
        def __init__(self, model, **kwargs):
            calls.append({"model": model, **kwargs})
            if error is not None:
                raise error

        def transcribe(self, audio, **kwargs):
            return [], None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisperModel)
    )
    return calls


def test_whisper_settings_defaults(monkeypatch):
    for name in WHISPER_ENV:
        monkeypatch.delenv(name, raising=False)
    config = Settings(openai_api_key="x", _env_file=None)
    assert config.whisper_model == "small"
    assert config.whisper_device == "cpu"
    assert config.whisper_compute_type == "int8"
    assert config.whisper_model_dir == "models/whisper"


def test_whisper_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL", "tiny")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float16")
    monkeypatch.setenv("WHISPER_MODEL_DIR", "/srv/whisper")
    config = Settings(openai_api_key="x", _env_file=None)
    assert config.whisper_model == "tiny"
    assert config.whisper_device == "cuda"
    assert config.whisper_compute_type == "float16"
    assert config.whisper_model_dir == "/srv/whisper"


@pytest.mark.parametrize("found, expected", [("C:/tools/ffmpeg.exe", True), (None, False)])
def test_health_reports_ffmpeg_without_changing_status(client, monkeypatch, found, expected):
    from app import main

    looked_up = []
    monkeypatch.setattr(main.shutil, "which", lambda name: looked_up.append(name) or found)
    assert client.get("/health").json() == {"status": "ok", "ffmpeg": expected}
    assert looked_up == ["ffmpeg"]


def test_importing_downloader_does_not_load_a_model(monkeypatch):
    calls = fake_faster_whisper(monkeypatch)
    monkeypatch.delitem(sys.modules, "app.voice.download_model", raising=False)
    importlib.import_module("app.voice.download_model")
    assert calls == []


def test_downloader_uses_configured_settings(monkeypatch, tmp_path, capsys):
    from app.voice import download_model

    calls = fake_faster_whisper(monkeypatch)
    target = tmp_path / "whisper"
    monkeypatch.setattr(settings, "whisper_model", "base")
    monkeypatch.setattr(settings, "whisper_device", "cpu")
    monkeypatch.setattr(settings, "whisper_compute_type", "int8")
    monkeypatch.setattr(settings, "whisper_model_dir", str(target))

    assert download_model.main() == 0
    assert calls == [
        {"model": "base", "device": "cpu", "compute_type": "int8", "download_root": str(target)}
    ]
    assert target.is_dir()
    assert f"Whisper model 'base' is ready in {target}" in capsys.readouterr().out


def test_downloader_relative_dir_resolves_from_repo_root(monkeypatch):
    from app.voice import download_model

    calls = fake_faster_whisper(monkeypatch)
    monkeypatch.setattr(Path, "mkdir", lambda self, **kwargs: None)
    monkeypatch.setattr(settings, "whisper_model_dir", "models/whisper")

    assert download_model.main() == 0
    assert calls[0]["download_root"] == str(ROOT / "models" / "whisper")


def test_downloader_failure_exits_nonzero_with_actionable_message(monkeypatch, tmp_path, capsys):
    from app.voice import download_model

    fake_faster_whisper(monkeypatch, error=ConnectionError("network unreachable"))
    monkeypatch.setattr(settings, "whisper_model", "small")
    monkeypatch.setattr(settings, "whisper_model_dir", str(tmp_path / "whisper"))

    assert download_model.main() == 1
    err = capsys.readouterr().err
    assert "Could not download or load Whisper model 'small'" in err
    assert "ConnectionError: network unreachable" in err
    assert "internet connection" in err
    assert "WHISPER_MODEL_DIR" in err


def test_downloader_reports_missing_package(monkeypatch, tmp_path, capsys):
    from app.voice import download_model

    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr(settings, "whisper_model_dir", str(tmp_path / "whisper"))

    assert download_model.main() == 1
    assert "python -m pip install -r requirements.txt" in capsys.readouterr().err
    assert not (tmp_path / "whisper").exists()


def test_runtime_loads_only_local_files_from_model_dir(monkeypatch, tmp_path):
    from app.voice.runtime import VoiceRuntime

    calls = fake_faster_whisper(monkeypatch)
    monkeypatch.setattr(settings, "voice_enabled", True)
    monkeypatch.setattr(settings, "whisper_model", "small")
    monkeypatch.setattr(settings, "whisper_model_dir", str(tmp_path))

    runtime = VoiceRuntime()
    asyncio.run(runtime.warm())
    try:
        assert runtime.state == "ready"
        assert calls == [
            {
                "model": "small",
                "device": settings.whisper_device,
                "compute_type": settings.whisper_compute_type,
                "download_root": str(tmp_path),
                "local_files_only": True,
            }
        ]
    finally:
        runtime.close()


def test_runtime_reports_missing_model_with_download_command(monkeypatch, tmp_path):
    from app.voice.runtime import VoiceRuntime

    fake_faster_whisper(monkeypatch, error=FileNotFoundError("not in cache"))
    monkeypatch.setattr(settings, "voice_enabled", True)
    monkeypatch.setattr(settings, "whisper_model_dir", str(tmp_path))

    runtime = VoiceRuntime()
    asyncio.run(runtime.warm())
    assert runtime.state == "error"
    assert "python -m app.voice.download_model" in runtime.error
    assert runtime.status() == {"status": "error"}


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_setup_files_agree_on_whisper_settings(monkeypatch):
    for name in WHISPER_ENV:
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(openai_api_key="x", _env_file=None)
    example = dict(
        line.split("=", 1)
        for line in read(".env.example").splitlines()
        if line.startswith("WHISPER_")
    )
    assert example == {name: getattr(defaults, name.lower()) for name in WHISPER_ENV}

    compose = read("docker-compose.yml")
    assert "\n  api:\n" in compose
    assert f"whisper-models:/app/{defaults.whisper_model_dir}" in compose
    assert "\nvolumes:\n  whisper-models:" in compose

    dockerfile = read("Dockerfile")
    assert "apt-get install -y --no-install-recommends ffmpeg" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    run_lines = [line for line in dockerfile.splitlines() if line.startswith("RUN")]
    assert not any("whisper" in line.lower() or "download" in line for line in run_lines)

    assert f"{defaults.whisper_model_dir}/" in read(".gitignore").splitlines()

    readme = read("README.md")
    assert "## Voice setup" in readme
    for text in [
        "winget install Gyan.FFmpeg",
        "choco install ffmpeg",
        "ffmpeg -version",
        "python -c \"import faster_whisper, gtts; print('voice imports OK')\"",
        "python -m app.voice.download_model",
        "docker compose up --build",
        "docker compose exec api",
        *WHISPER_ENV,
    ]:
        assert text in readme
