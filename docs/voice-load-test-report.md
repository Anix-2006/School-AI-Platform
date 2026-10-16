# Voice load test — CPU vs GPU decision (issue #55)

**Status of measured runs in this document:** local CPU runs completed on
2026-10-16 against a single-worker uvicorn process with `VOICE_ENABLED=true`,
Whisper `tiny` / `cpu` / `int8`, and the issue #54 transcription queue
(`VOICE_MAX_PARALLEL=1`). GPU comparison was **not** run.

## 1. What was measured

| Concurrency | Attempted | Successes | Failure % | Median total (s) | Slowest total (s) | Avg queue wait (s) | Peak container CPU % | Peak container memory |
|---|---|---|---|---|---|---|---|---|
| 5 | 5 | 5 | 0.0% | 1.314 | 2.519 | 0.862 (n=5) | NA (no Docker) | NA (no Docker) |
| 10 | 10 | 10 | 0.0% | 2.205 | 4.065 | 1.742 (n=10) | NA (no Docker) | NA (no Docker) |
| 20 | 20 | 17 | 15.0% | 4.059 | 7.584 | 3.476 (n=17) | NA (no Docker) | NA (no Docker) |

GPU comparison (`WHISPER_DEVICE=cuda`, `WHISPER_COMPUTE_TYPE=float16`): **NOT RUN**

Artifacts from this run (local; `benchmark_results/` is gitignored):

```text
benchmark_results/load-2026-10-16T171914Z-c5-20261016T171914Z-78d6df.csv
benchmark_results/summary-2026-10-16T171914Z-c5-20261016T171914Z-78d6df.json
benchmark_results/load-2026-10-16T171924Z-c10-20261016T171924Z-c30a0d.csv
benchmark_results/summary-2026-10-16T171924Z-c10-20261016T171924Z-c30a0d.json
benchmark_results/load-2026-10-16T171934Z-c20-20261016T171934Z-6dc079.csv
benchmark_results/summary-2026-10-16T171934Z-c20-20261016T171934Z-6dc079.json
```

Notes from the summaries:
- Failures at concurrency 20 were **HTTP errors** (3), not timeouts/connection errors.
- Queue wait is server-reported `timings_ms.queue` (seconds).
- Container CPU/memory unavailable because Docker was not installed/on PATH.

## 2. Documented performance targets in this repository

`RUNNING.md` defines **verification templates** for latency (p50/p95 over 15
questions) but explicitly marks those figures as **NOT YET MEASURED**. There is
**no numeric SLA** in-repo that this load test can “pass” or “fail” against.

Judgement below is based on observed failure rate, median/worst client latency,
and server queue wait under 5 / 10 / 20 concurrent `/voice/chat` requests.

## 3. Assumptions and unavailable data

- Endpoint under test is **`POST /voice/chat`**. Auth is
  `GET /auth/demo-token` → `Authorization: Bearer …`.
- Clips were synthetic WAVs in `tests/fixtures/voice_load/` (not real parent audio).
- Guardians were consented demo workbook rows (aliases only in CSVs).
- Host CPU/RAM were not sampled; only optional `docker stats` is implemented.
- GPU path was not exercised — no CUDA comparison numbers exist yet.
- The #54 queue with `VOICE_MAX_PARALLEL=1` serializes STT: waiting grows with
  concurrency (0.86s → 1.74s → 3.48s average queue wait).

## 4. Recommendation (from these CPU runs)

### Hardware

- **CPU for pilot:** **Yes, for expected bursts around ≤10 concurrent voice notes**
  on this machine/config (0% failures at 5 and 10; median totals ~1.3s / ~2.2s).
- **GPU required:** **Not proven.** At concurrency **20**, CPU already shows
  **15% HTTP failures**, median ~4.1s, slowest ~7.6s, and avg queue wait ~3.5s.
  That argues for keeping pilot concurrency modest **or** re-testing with GPU /
  higher `VOICE_MAX_PARALLEL` before promising 20-wide bursts. GPU comparison
  remains **NOT RUN**.

### Suggested settings

| Setting | Recommendation | Reason |
|---|---|---|
| `VOICE_MAX_PARALLEL` | Keep `1` for small CPU pilots; consider `2` only after a re-run | Parallel=1 was stable at 5–10; raising may cut queue wait but needs measurement |
| `WHISPER_MODEL` | Keep `tiny` for pilot | Used in this run; larger models need more CPU/RAM |
| `WHISPER_COMPUTE_TYPE` | `int8` on CPU | Matches this run |
| `WHISPER_DEVICE` | `cpu` until a CUDA re-run | GPU path not measured |

### How to run the GPU comparison when hardware exists

```powershell
$env:VOICE_ENABLED="true"
$env:WHISPER_DEVICE="cuda"
$env:WHISPER_COMPUTE_TYPE="float16"
# start API with single worker, wait until /voice/status is ready

python -m app.voice.loadtest --concurrent 5 --warmup 1
python -m app.voice.loadtest --concurrent 10 --warmup 1
python -m app.voice.loadtest --concurrent 20 --warmup 1
```

Prerequisites: NVIDIA GPU, drivers, CUDA/cuDNN compatible with `faster-whisper`
/ `ctranslate2`, and GPU passthrough if using Docker.

## 5. Decision checklist for the PR

- [x] Recorded CPU results for concurrency 5, 10, 20
- [x] Noted stress point: failures begin at concurrency **20** (15% HTTP errors)
- [x] Recommended `VOICE_MAX_PARALLEL=1`, `tiny`, `int8`, `cpu` for ≤10 bursts
- [x] GPU comparison explicitly **NOT RUN**
