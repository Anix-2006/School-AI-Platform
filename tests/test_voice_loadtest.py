"""Unit tests for app.voice.loadtest (issue #55) — no live Whisper/Docker/GPU."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.voice.loadtest.clips import cycle_clips, discover_clips
from app.voice.loadtest.cli import build_parser, main
from app.voice.loadtest.docker_monitor import parse_docker_stats_line
from app.voice.loadtest.guardians import cycle_guardians, load_approved_guardians
from app.voice.loadtest.runner import (
    SUPPORTED_CONCURRENCY,
    LoadtestConfig,
    CSV_COLUMNS,
    _error_category,
    _queue_wait_seconds,
    run_loadtest,
    validate_concurrency,
)
from app.voice.loadtest.stats import format_summary, summarize_level


CLIPS = Path(__file__).resolve().parent / "fixtures" / "voice_load"


def test_supported_concurrency_values():
    for value in (5, 10, 20):
        assert validate_concurrency(value) == value
    with pytest.raises(ValueError, match="Unsupported concurrency"):
        validate_concurrency(7)


def test_cli_rejects_invalid_concurrency(capsys):
    with pytest.raises(SystemExit) as exited:
        main(["--concurrent", "3", "--dry-run"])
    assert exited.value.code == 2


def test_discover_and_cycle_clips():
    clips = discover_clips(CLIPS)
    assert len(clips) >= 5
    plan = cycle_clips(clips, 12)
    assert len(plan) == 12
    assert plan[0].name != plan[1].name or len(clips) == 1
    assert {p.name for p in plan[:5]} <= {p.name for p in clips}


def test_approved_guardians_loaded_from_workbook_with_consent_only():
    from tests.conftest import SOURCE_WORKBOOK

    approved = load_approved_guardians(SOURCE_WORKBOOK)
    assert len(approved) >= 5
    assert len({g.guardian_id for g in approved}) == len(approved)
    assert all(g.alias.startswith("parent-") for g in approved)
    # Guardians without consent_given_at must not appear.
    from app.services.excel_store import ExcelStore

    store = ExcelStore(SOURCE_WORKBOOK)
    consented = {
        str(row["id"])
        for row in store.rows("guardians")
        if row.get("consent_given_at")
    }
    assert {g.guardian_id for g in approved} <= consented
    plan = cycle_guardians(12, SOURCE_WORKBOOK)
    assert len(plan) == 12
    assert plan[0].alias == approved[0].alias


def test_queue_wait_unavailable_is_na_not_fabricated():
    assert _queue_wait_seconds(None) == "NA"
    assert _queue_wait_seconds({}) == "NA"
    assert _queue_wait_seconds({"timings": {"queue": 12}}) == "NA"
    assert _queue_wait_seconds({"timings_ms": {"queue": 1500}}) == 1.5


def test_error_categories():
    assert _error_category(None, 200) == ""
    assert _error_category(httpx.TimeoutException("t"), None) == "timeout"
    assert _error_category(httpx.ConnectError("c"), None) == "connection_error"
    assert _error_category(None, 503) == "http_error"


def test_summary_math_and_zero_success():
    rows = [
        {
            "success": True,
            "total_seconds": 1.0,
            "queue_wait_seconds": 0.2,
            "error_category": "",
        },
        {
            "success": True,
            "total_seconds": 3.0,
            "queue_wait_seconds": 0.4,
            "error_category": "",
        },
        {
            "success": False,
            "total_seconds": 9.0,
            "queue_wait_seconds": "NA",
            "error_category": "timeout",
        },
    ]
    summary = summarize_level(concurrency=5, rows=rows)
    assert summary.attempted == 3
    assert summary.successes == 2
    assert summary.failures == 1
    assert summary.failure_pct == pytest.approx(100 / 3)
    assert summary.median_total_seconds == 2.0
    assert summary.max_total_seconds == 3.0
    assert summary.avg_queue_wait_seconds == pytest.approx(0.3)
    assert summary.queue_wait_samples == 2
    assert summary.timeout_count == 1

    empty = summarize_level(concurrency=5, rows=[])
    text = format_summary([empty])
    assert "median total" in text
    assert "NA" in text


def test_docker_stats_parsing():
    sample = parse_docker_stats_line(
        {
            "CPUPerc": "12.5%",
            "MemUsage": "256MiB / 2GiB",
        }
    )
    assert sample.cpu_percent == 12.5
    assert sample.memory_bytes == pytest.approx(256 * 1024 * 1024)
    assert sample.memory_limit_bytes == pytest.approx(2 * 1024**3)


@pytest.mark.asyncio
async def test_concurrent_http_burst_and_csv(tmp_path):
    started: list[float] = []
    barrier_hits = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal barrier_hits
        path = request.url.path
        if path.endswith("/auth/demo-token"):
            return httpx.Response(200, json={"access_token": "test-token"})
        if path.endswith("/voice/status"):
            return httpx.Response(200, json={"status": "ready"})
        if path.endswith("/health"):
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/voice/chat"):
            import time

            started.append(time.perf_counter())
            barrier_hits += 1
            # Hold briefly so overlapping starts are observable.
            await asyncio.sleep(0.05)
            body = request.content
            assert b"guardian_id" in body
            assert b"student_id" in body
            assert b"audio" in body
            assert b"test-token" not in body  # token is in header, not body
            assert request.headers.get("Authorization") == "Bearer test-token"
            return httpx.Response(
                200,
                json={
                    "transcript": "secret-should-not-be-logged",
                    "reply": "ok",
                    "agent_used": "parent_teacher",
                    "language": "en",
                    "audio_id": None,
                    "timings": {"queue": 10, "stt": 20, "llm": 30, "tts": 0, "total": 60},
                    "timings_ms": {
                        "upload": 1,
                        "decode": 1,
                        "queue": 250,
                        "stt": 20,
                        "router": 0,
                        "agent_llm": 30,
                        "tts": 0,
                        "total": 302,
                    },
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    async def _run_with_transport(config: LoadtestConfig):
        # Patch AsyncClient used inside run_loadtest by monkeypatching httpx in runner.
        import app.voice.loadtest.runner as module

        real_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        module.httpx.AsyncClient = factory  # type: ignore[attr-defined]
        try:
            return await run_loadtest(config)
        finally:
            module.httpx.AsyncClient = real_client  # type: ignore[attr-defined]

    config = LoadtestConfig(
        base_url="http://test",
        concurrency=5,
        timeout=5.0,
        output_dir=tmp_path,
        clips_dir=CLIPS,
        warmup=0,
        settle_seconds=0,
        token="test-token",
    )
    artifacts = await _run_with_transport(config)
    assert artifacts.results_csv.is_file()
    text = artifacts.results_csv.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(CSV_COLUMNS)
    assert text.count("\n") == 6  # header + 5
    assert "secret-should-not-be-logged" not in text
    assert "test-token" not in text
    assert "Sneha" not in text
    assert "parent-a" in text
    assert barrier_hits == 5
    # Starts should cluster (barrier), not serialize across 5*0.05s fully.
    assert max(started) - min(started) < 0.2
    assert artifacts.summary is not None
    assert artifacts.summary.successes == 5
    assert artifacts.summary.avg_queue_wait_seconds == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_http_timeout_and_connection_rows(tmp_path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/demo-token"):
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path.endswith("/voice/chat"):
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    import app.voice.loadtest.runner as module

    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)

    async def _resolve(*_a, **_k):
        return None

    monkeypatch.setattr(module, "resolve_container_name", _resolve)

    artifacts = await run_loadtest(
        LoadtestConfig(
            base_url="http://test",
            concurrency=5,
            timeout=1.0,
            output_dir=tmp_path / "conn",
            clips_dir=CLIPS,
            settle_seconds=0,
            token="t",
        )
    )
    assert all(row["error_category"] == "connection_error" for row in artifacts.rows)
    assert artifacts.summary.successes == 0


def test_repeated_runs_do_not_overwrite(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/voice/chat"):
            return httpx.Response(
                200,
                json={
                    "timings_ms": {"queue": 0},
                    "reply": "x",
                    "transcript": "y",
                    "agent_used": None,
                    "language": "en",
                    "audio_id": None,
                    "timings": {"queue": 0, "stt": 0, "llm": 0, "tts": 0, "total": 0},
                },
            )
        if request.url.path.endswith("/auth/demo-token"):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"status": "ready"})

    transport = httpx.MockTransport(handler)

    async def once():
        import app.voice.loadtest.runner as module

        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        module.httpx.AsyncClient = factory  # type: ignore[misc]

        async def _resolve(*_a, **_k):
            return None

        module.resolve_container_name = _resolve  # type: ignore[assignment]
        try:
            return await run_loadtest(
                LoadtestConfig(
                    base_url="http://test",
                    concurrency=5,
                    output_dir=tmp_path,
                    clips_dir=CLIPS,
                    settle_seconds=0,
                    token="t",
                )
            )
        finally:
            module.httpx.AsyncClient = real  # type: ignore[misc]

    a = asyncio.run(once())
    b = asyncio.run(once())
    assert a.results_csv != b.results_csv
    assert a.results_csv.is_file() and b.results_csv.is_file()


def test_parser_lists_required_options():
    parser = build_parser()
    help_text = parser.format_help()
    assert "--concurrent" in help_text
    assert "--base-url" in help_text
    assert "--dry-run" in help_text
    assert str(sorted(SUPPORTED_CONCURRENCY)) in help_text or "5" in help_text
