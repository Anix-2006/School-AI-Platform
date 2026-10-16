"""Concurrent HTTP load runner for POST /voice/chat."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.voice.loadtest.clips import content_type_for, cycle_clips, discover_clips
from app.voice.loadtest.docker_monitor import (
    DockerMonitorUnavailable,
    ResourceSample,
    monitor_loop,
    resolve_container_name,
    write_resource_csv,
)
from app.voice.loadtest.guardians import TestGuardian, cycle_guardians
from app.voice.loadtest.stats import (
    LevelSummary,
    format_summary,
    summarize_level,
    summary_to_dict,
)

logger = logging.getLogger(__name__)

SUPPORTED_CONCURRENCY = frozenset({5, 10, 20})
DEFAULT_ENDPOINT = "/voice/chat"
DEFAULT_OUTPUT_DIR = Path("benchmark_results")
CSV_COLUMNS = [
    "run_id",
    "timestamp",
    "concurrency",
    "request_id",
    "guardian_alias",
    "clip_id",
    "start_time",
    "end_time",
    "total_seconds",
    "queue_wait_seconds",
    "http_status",
    "success",
    "error_category",
]


@dataclass
class LoadtestConfig:
    base_url: str = "http://127.0.0.1:8000"
    endpoint: str = DEFAULT_ENDPOINT
    concurrency: int = 5
    timeout: float = 120.0
    output_dir: Path = DEFAULT_OUTPUT_DIR
    clips_dir: Path | None = None
    docker_container: str | None = None
    docker_compose_service: str = "api"
    sample_interval: float = 1.0
    warmup: int = 0
    token: str | None = None
    dry_run: bool = False
    settle_seconds: float = 2.0


@dataclass
class RunArtifacts:
    run_id: str
    concurrency: int
    results_csv: Path
    resources_csv: Path | None
    summary_json: Path
    rows: list[dict[str, Any]] = field(default_factory=list)
    summary: LevelSummary | None = None


def validate_concurrency(value: int) -> int:
    if value not in SUPPORTED_CONCURRENCY:
        raise ValueError(
            f"Unsupported concurrency {value}. "
            f"Use one of: {sorted(SUPPORTED_CONCURRENCY)}"
        )
    return value


async def fetch_demo_token(client: httpx.AsyncClient, base_url: str) -> str:
    response = await client.get(f"{base_url.rstrip('/')}/auth/demo-token")
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("demo-token response missing access_token")
    return str(token)


async def preflight(config: LoadtestConfig) -> dict[str, Any]:
    """Validate config, clips, guardians, and endpoint reachability (no audio)."""
    validate_concurrency(config.concurrency)
    clips = discover_clips(config.clips_dir)
    guardians = cycle_guardians(config.concurrency)
    report: dict[str, Any] = {
        "base_url": config.base_url,
        "endpoint": config.endpoint,
        "concurrency": config.concurrency,
        "clip_count": len(clips),
        "clip_ids": [p.name for p in clips[:10]],
        "guardian_aliases": [g.alias for g in guardians],
        "health": None,
        "voice_status": None,
        "token_ok": False,
        "docker_container": None,
        "ok": False,
        "errors": [],
    }
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            health = await client.get(f"{config.base_url.rstrip('/')}/health")
            report["health"] = health.status_code
            if health.status_code != 200:
                report["errors"].append(f"health returned {health.status_code}")
        except httpx.HTTPError as exc:
            report["errors"].append(f"health unreachable: {type(exc).__name__}")
            return report
        try:
            token = config.token or await fetch_demo_token(client, config.base_url)
            report["token_ok"] = True
        except Exception as exc:
            report["errors"].append(f"auth failed: {type(exc).__name__}")
            return report
        try:
            status = await client.get(
                f"{config.base_url.rstrip('/')}/voice/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            report["voice_status"] = status.json() if status.status_code == 200 else {
                "http_status": status.status_code
            }
            if status.status_code != 200:
                report["errors"].append(
                    f"voice/status returned {status.status_code} "
                    "(is VOICE_ENABLED=true and the model ready?)"
                )
            elif status.json().get("status") != "ready":
                report["errors"].append(
                    f"voice status is {status.json().get('status')!r}, need 'ready'"
                )
        except httpx.HTTPError as exc:
            report["errors"].append(f"voice/status failed: {type(exc).__name__}")
    report["docker_container"] = await resolve_container_name(
        config.docker_container, config.docker_compose_service
    )
    report["ok"] = not report["errors"]
    return report


def _queue_wait_seconds(payload: dict[str, Any] | None) -> str | float:
    """Return server-reported queue wait in seconds, or 'NA' if unavailable."""
    if not isinstance(payload, dict):
        return "NA"
    timings = payload.get("timings_ms")
    if not isinstance(timings, dict) or "queue" not in timings:
        # Do not invent values from client latency.
        return "NA"
    try:
        return round(float(timings["queue"]) / 1000.0, 6)
    except (TypeError, ValueError):
        return "NA"


def _error_category(exc: BaseException | None, status: int | None) -> str:
    if exc is None and status is not None and 200 <= status < 300:
        return ""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (httpx.ConnectError, httpx.TransportError)):
        return "connection_error"
    if status is not None and status >= 400:
        return "http_error"
    if exc is not None:
        return "other"
    return "http_error"


async def _one_request(
    client: httpx.AsyncClient,
    *,
    url: str,
    token: str,
    guardian: TestGuardian,
    clip: Path,
    barrier: asyncio.Barrier,
    concurrency: int,
    run_id: str,
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    clip_id = clip.name
    started = 0.0
    ended = 0.0
    status: int | None = None
    success = False
    queue_wait: str | float = "NA"
    exc: BaseException | None = None
    await barrier.wait()
    started = time.perf_counter()
    wall_start = time.time()
    try:
        with clip.open("rb") as handle:
            files = {
                "audio": (clip.name, handle, content_type_for(clip)),
            }
            data = {
                "guardian_id": guardian.guardian_id,
                "student_id": guardian.student_id,
                "language": guardian.language,
                "session_id": f"loadtest-{run_id}-{request_id}",
            }
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                files=files,
                data=data,
            )
        status = response.status_code
        success = 200 <= status < 300
        if success:
            try:
                queue_wait = _queue_wait_seconds(response.json())
            except json.JSONDecodeError:
                queue_wait = "NA"
                success = False
                exc = ValueError("invalid_json")
        else:
            # Never log response bodies (may contain transcripts).
            pass
    except BaseException as error:
        exc = error
    finally:
        ended = time.perf_counter()
        wall_end = time.time()
    return {
        "run_id": run_id,
        "timestamp": datetime.fromtimestamp(wall_start, tz=timezone.utc).isoformat(),
        "concurrency": concurrency,
        "request_id": request_id,
        "guardian_alias": guardian.alias,
        "clip_id": clip_id,
        "start_time": f"{wall_start:.6f}",
        "end_time": f"{wall_end:.6f}",
        "total_seconds": round(ended - started, 6),
        "queue_wait_seconds": queue_wait,
        "http_status": status if status is not None else "",
        "success": success,
        "error_category": _error_category(exc, status),
    }


def _write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_COLUMNS})


async def run_loadtest(config: LoadtestConfig) -> RunArtifacts:
    validate_concurrency(config.concurrency)
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    results_csv = out / f"load-{stamp}-c{config.concurrency}-{run_id}.csv"
    summary_json = out / f"summary-{stamp}-c{config.concurrency}-{run_id}.json"
    resources_csv: Path | None = out / f"resources-{stamp}-c{config.concurrency}-{run_id}.csv"

    if config.dry_run:
        report = await preflight(config)
        summary_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not report["ok"]:
            raise RuntimeError(
                "preflight failed: " + "; ".join(report["errors"])
            )
        return RunArtifacts(
            run_id=run_id,
            concurrency=config.concurrency,
            results_csv=results_csv,
            resources_csv=None,
            summary_json=summary_json,
            rows=[],
            summary=None,
        )

    clips = discover_clips(config.clips_dir)
    clip_plan = cycle_clips(clips, config.concurrency)
    guardian_plan = cycle_guardians(config.concurrency)
    url = f"{config.base_url.rstrip('/')}{config.endpoint}"
    timeout = httpx.Timeout(config.timeout, connect=min(10.0, config.timeout))

    samples: list[ResourceSample] = []
    stop = asyncio.Event()
    monitor_task: asyncio.Task | None = None
    container = await resolve_container_name(
        config.docker_container, config.docker_compose_service
    )
    if container:
        monitor_task = asyncio.create_task(
            monitor_loop(container, stop, config.sample_interval, samples)
        )
    else:
        resources_csv = None
        logger.info("docker_monitor_skipped reason=unavailable")

    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        token = config.token or os.environ.get("LOADTEST_TOKEN")
        if not token:
            token = await fetch_demo_token(client, config.base_url)

        # Warm-up requests are excluded from measured results.
        for index in range(max(0, config.warmup)):
            guardian = guardian_plan[index % len(guardian_plan)]
            clip = clip_plan[index % len(clip_plan)]
            barrier = asyncio.Barrier(1)
            await _one_request(
                client,
                url=url,
                token=token,
                guardian=guardian,
                clip=clip,
                barrier=barrier,
                concurrency=1,
                run_id=f"{run_id}-warmup",
            )
            logger.info("warmup_complete index=%s", index)

        barrier = asyncio.Barrier(config.concurrency)
        tasks = [
            asyncio.create_task(
                _one_request(
                    client,
                    url=url,
                    token=token,
                    guardian=guardian_plan[i],
                    clip=clip_plan[i],
                    barrier=barrier,
                    concurrency=config.concurrency,
                    run_id=run_id,
                )
            )
            for i in range(config.concurrency)
        ]
        rows = list(await asyncio.gather(*tasks))

    stop.set()
    if monitor_task is not None:
        try:
            await asyncio.wait_for(monitor_task, timeout=config.sample_interval + 2)
        except (asyncio.TimeoutError, DockerMonitorUnavailable):
            monitor_task.cancel()
        except Exception:
            monitor_task.cancel()

    _write_results_csv(results_csv, rows)
    if resources_csv is not None:
        write_resource_csv(
            resources_csv,
            run_id=run_id,
            concurrency=config.concurrency,
            samples=samples,
        )

    summary = summarize_level(
        concurrency=config.concurrency,
        rows=rows,
        cpu_samples=[s.cpu_percent for s in samples],
        memory_samples=[s.memory_bytes for s in samples],
    )
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "config": {
            "base_url": config.base_url,
            "endpoint": config.endpoint,
            "concurrency": config.concurrency,
            "timeout": config.timeout,
            "warmup": config.warmup,
            "docker_container": container,
        },
        "queue_wait_note": (
            "queue_wait_seconds is server-reported timings_ms.queue / 1000 when "
            "present; otherwise NA. Client total_seconds is wall time and is "
            "not used as a substitute for queue wait."
        ),
        "summary": summary_to_dict([summary]),
        "results_csv": str(results_csv),
        "resources_csv": str(resources_csv) if resources_csv else None,
    }
    summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(format_summary([summary]))
    if config.settle_seconds > 0:
        await asyncio.sleep(config.settle_seconds)
    return RunArtifacts(
        run_id=run_id,
        concurrency=config.concurrency,
        results_csv=results_csv,
        resources_csv=resources_csv,
        summary_json=summary_json,
        rows=rows,
        summary=summary,
    )
