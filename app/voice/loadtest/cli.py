"""CLI for ``python -m app.voice.loadtest``."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app.voice.loadtest.runner import (
    SUPPORTED_CONCURRENCY,
    LoadtestConfig,
    preflight,
    run_loadtest,
    validate_concurrency,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Load-test POST /voice/chat with concurrent multipart uploads "
            "(issue #55). Supported concurrency levels: "
            f"{sorted(SUPPORTED_CONCURRENCY)}."
        )
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        required=True,
        help=f"Simultaneous requests; one of {sorted(SUPPORTED_CONCURRENCY)}",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LOADTEST_BASE_URL", "http://127.0.0.1:8000"),
        help="API base URL (env LOADTEST_BASE_URL)",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("LOADTEST_ENDPOINT", "/voice/chat"),
        help="Voice upload path (default /voice/chat — the real FastAPI route)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("LOADTEST_TIMEOUT", "120")),
        help="Per-request timeout in seconds",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("LOADTEST_OUTPUT_DIR", "benchmark_results")),
        help="Directory for CSV/JSON artifacts (new timestamped files each run)",
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        default=None,
        help="Directory of approved test clips (default tests/fixtures/voice_load)",
    )
    parser.add_argument(
        "--docker-container",
        default=os.environ.get("LOADTEST_DOCKER_CONTAINER"),
        help="Container name for docker stats (auto-detect compose service api)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=int(os.environ.get("LOADTEST_WARMUP", "0")),
        help="Unmeasured warm-up requests before the timed burst",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Pause after the run so the next level can start on a quieter process",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("LOADTEST_TOKEN"),
        help="Bearer token (default: GET /auth/demo-token; env LOADTEST_TOKEN)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preflight only: auth, /voice/status, clips, guardians — no audio upload",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Alias for --dry-run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_concurrency(args.concurrent)
    except ValueError as exc:
        parser.error(str(exc))

    config = LoadtestConfig(
        base_url=args.base_url,
        endpoint=args.endpoint,
        concurrency=args.concurrent,
        timeout=args.timeout,
        output_dir=args.output_dir,
        clips_dir=args.clips_dir,
        docker_container=args.docker_container,
        warmup=args.warmup,
        settle_seconds=args.settle_seconds,
        token=args.token,
        dry_run=bool(args.dry_run or args.preflight),
    )

    if config.dry_run:
        report = asyncio.run(preflight(config))
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 2

    try:
        artifacts = asyncio.run(run_loadtest(config))
    except Exception as exc:
        print(f"loadtest failed: {exc}", file=sys.stderr)
        return 1
    print(f"results_csv={artifacts.results_csv}")
    if artifacts.resources_csv:
        print(f"resources_csv={artifacts.resources_csv}")
    print(f"summary_json={artifacts.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
