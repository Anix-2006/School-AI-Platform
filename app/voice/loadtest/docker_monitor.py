"""Optional container CPU/memory sampling via ``docker stats``.

Metrics are **container-level**, not host-level. Monitoring is best-effort:
missing Docker or permissions must not abort the HTTP benchmark.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResourceSample:
    timestamp: float
    cpu_percent: float
    memory_bytes: float
    memory_limit_bytes: float | None


class DockerMonitorUnavailable(Exception):
    pass


def docker_available() -> bool:
    return shutil.which("docker") is not None


async def resolve_container_name(
    preferred: str | None = None,
    compose_service: str = "api",
) -> str | None:
    """Return a running container name, or None if Docker is unavailable."""
    if not docker_available():
        return None
    if preferred:
        return preferred
    # Prefer compose project service ``api`` from docker-compose.yml.
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            "--format",
            "{{.Names}}\t{{.Label \"com.docker.compose.service\"}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "docker_ps_failed detail=%s",
                (err or b"").decode("utf-8", errors="replace")[:200],
            )
            return None
        for line in out.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            name = parts[0].strip()
            service = parts[1].strip() if len(parts) > 1 else ""
            if service == compose_service or compose_service in name:
                return name
        # Fallback: first running container.
        lines = [ln for ln in out.decode().splitlines() if ln.strip()]
        if lines:
            return lines[0].split("\t")[0].strip()
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("docker_resolve_failed", exc_info=True)
        return None
    return None


def parse_docker_stats_line(payload: dict[str, Any] | str) -> ResourceSample:
    """Parse one ``docker stats --format '{{json .}}'`` object."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    cpu = _parse_percent(data.get("CPUPerc") or data.get("cpu_percent") or "0%")
    mem_raw = data.get("MemUsage") or data.get("memory") or "0B / 0B"
    used, limit = _parse_mem_usage(str(mem_raw))
    return ResourceSample(
        timestamp=time.time(),
        cpu_percent=cpu,
        memory_bytes=used,
        memory_limit_bytes=limit,
    )


def _parse_percent(value: str) -> float:
    text = str(value).strip().rstrip("%")
    return float(text) if text else 0.0


def _parse_mem_usage(value: str) -> tuple[float, float | None]:
    # Examples: "123.4MiB / 1.5GiB", "100MB / 2GB"
    left, _, right = value.partition("/")
    used = _parse_bytes(left.strip())
    limit = _parse_bytes(right.strip()) if right.strip() else None
    return used, limit


def _parse_bytes(value: str) -> float:
    text = value.strip()
    units = (
        ("TiB", 1024**4),
        ("GiB", 1024**3),
        ("MiB", 1024**2),
        ("KiB", 1024),
        ("TB", 1000**3),
        ("GB", 1000**3),
        ("MB", 1000**2),
        ("KB", 1000),
        ("B", 1),
    )
    for suffix, mult in units:
        if text.endswith(suffix):
            return float(text[: -len(suffix)].strip()) * mult
    return float(text)


async def sample_stats(container: str) -> ResourceSample:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "stats",
        container,
        "--no-stream",
        "--format",
        "{{json .}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        detail = (err or b"").decode("utf-8", errors="replace")[:200]
        raise DockerMonitorUnavailable(detail or "docker stats failed")
    line = out.decode("utf-8", errors="replace").strip().splitlines()[0]
    return parse_docker_stats_line(line)


async def monitor_loop(
    container: str,
    stop: asyncio.Event,
    interval: float,
    sink: list[ResourceSample],
) -> None:
    while not stop.is_set():
        try:
            sink.append(await sample_stats(container))
        except Exception as exc:
            logger.warning(
                "docker_stats_sample_failed error=%s",
                type(exc).__name__,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def write_resource_csv(
    path: Path,
    *,
    run_id: str,
    concurrency: int,
    samples: list[ResourceSample],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "concurrency",
                "timestamp",
                "cpu_percent",
                "memory_bytes",
                "memory_limit_bytes",
                "scope",
            ],
        )
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "run_id": run_id,
                    "concurrency": concurrency,
                    "timestamp": f"{sample.timestamp:.3f}",
                    "cpu_percent": f"{sample.cpu_percent:.2f}",
                    "memory_bytes": f"{sample.memory_bytes:.0f}",
                    "memory_limit_bytes": (
                        f"{sample.memory_limit_bytes:.0f}"
                        if sample.memory_limit_bytes is not None
                        else ""
                    ),
                    "scope": "container",
                }
            )
