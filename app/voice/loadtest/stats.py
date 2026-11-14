"""Summary statistics for load-test results (no fabricated values)."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence


@dataclass
class LevelSummary:
    concurrency: int
    attempted: int
    successes: int
    failures: int
    failure_pct: float
    median_total_seconds: float | None
    max_total_seconds: float | None
    avg_queue_wait_seconds: float | None
    queue_wait_samples: int
    timeout_count: int
    connection_error_count: int
    http_error_count: int
    other_error_count: int
    avg_cpu_percent: float | None
    peak_cpu_percent: float | None
    avg_memory_bytes: float | None
    peak_memory_bytes: float | None
    missing_notes: list[str]


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(statistics.mean(values))


def summarize_level(
    *,
    concurrency: int,
    rows: Sequence[dict[str, Any]],
    cpu_samples: Sequence[float] = (),
    memory_samples: Sequence[float] = (),
) -> LevelSummary:
    attempted = len(rows)
    successes = sum(1 for r in rows if r.get("success"))
    failures = attempted - successes
    totals = [
        float(r["total_seconds"])
        for r in rows
        if r.get("success") and r.get("total_seconds") is not None
    ]
    queue_waits = [
        float(r["queue_wait_seconds"])
        for r in rows
        if r.get("queue_wait_seconds") not in (None, "", "NA")
    ]
    notes: list[str] = []
    if not queue_waits:
        notes.append(
            "queue_wait_seconds unavailable for all rows "
            "(response missing timings_ms.queue or request failed)"
        )
    if not cpu_samples:
        notes.append("container CPU samples unavailable")
    if not memory_samples:
        notes.append("container memory samples unavailable")

    def _count(cat: str) -> int:
        return sum(1 for r in rows if r.get("error_category") == cat)

    return LevelSummary(
        concurrency=concurrency,
        attempted=attempted,
        successes=successes,
        failures=failures,
        failure_pct=(100.0 * failures / attempted) if attempted else 0.0,
        median_total_seconds=_median(totals),
        max_total_seconds=max(totals) if totals else None,
        avg_queue_wait_seconds=_mean(queue_waits),
        queue_wait_samples=len(queue_waits),
        timeout_count=_count("timeout"),
        connection_error_count=_count("connection_error"),
        http_error_count=_count("http_error"),
        other_error_count=_count("other"),
        avg_cpu_percent=_mean(list(cpu_samples)),
        peak_cpu_percent=max(cpu_samples) if cpu_samples else None,
        avg_memory_bytes=_mean(list(memory_samples)),
        peak_memory_bytes=max(memory_samples) if memory_samples else None,
        missing_notes=notes,
    )


def format_summary(levels: Iterable[LevelSummary]) -> str:
    lines = ["Voice load-test summary", "======================="]
    for level in levels:
        lines.append(f"\nConcurrency {level.concurrency}")
        lines.append(f"  attempted:     {level.attempted}")
        lines.append(f"  successes:     {level.successes}")
        lines.append(
            f"  failures:      {level.failures} ({level.failure_pct:.1f}%)"
        )
        lines.append(
            f"  median total:  {_fmt_s(level.median_total_seconds)}"
        )
        lines.append(f"  slowest total: {_fmt_s(level.max_total_seconds)}")
        lines.append(
            f"  avg queue wait:{_fmt_s(level.avg_queue_wait_seconds)} "
            f"(n={level.queue_wait_samples} valid samples)"
        )
        lines.append(
            f"  errors:        timeout={level.timeout_count} "
            f"connection={level.connection_error_count} "
            f"http={level.http_error_count} other={level.other_error_count}"
        )
        lines.append(
            f"  container CPU: avg={_fmt_pct(level.avg_cpu_percent)} "
            f"peak={_fmt_pct(level.peak_cpu_percent)}"
        )
        lines.append(
            f"  container mem:  avg={_fmt_bytes(level.avg_memory_bytes)} "
            f"peak={_fmt_bytes(level.peak_memory_bytes)}"
        )
        for note in level.missing_notes:
            lines.append(f"  note: {note}")
    return "\n".join(lines) + "\n"


def summary_to_dict(levels: Sequence[LevelSummary]) -> dict[str, Any]:
    return {"levels": [asdict(level) for level in levels]}


def _fmt_s(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return " NA"
    return f" {value:.3f}s"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.1f}%"


def _fmt_bytes(value: float | None) -> str:
    if value is None:
        return "NA"
    mb = value / (1024 * 1024)
    return f"{mb:.1f} MiB"
