# Running and deploying

## Local setup (Python 3.11)

Install Python 3.11, then run from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `OPENAI_API_KEY` in `.env`. Keep `VOICE_ENABLED=false` for text-only
operation, or set it to `true` to enable voice. Start exactly one worker:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

On macOS/Linux, create the environment with
`python3.11 -m venv .venv`, activate with `source .venv/bin/activate`, copy with
`cp .env.example .env`, and use the same install/start commands.

The single worker is required by the workbook-backed store and also keeps the
voice model, limits, and audio cache in one process. Close
`data/school_data.xlsx` in Excel before testing writes.

## Configuration

`.env.example` lists every setting read by `app/config.py`. Voice defaults are:

- `VOICE_ENABLED=false`: voice routes return 404 until explicitly enabled.
- `WHISPER_MODEL=tiny`, `WHISPER_COMPUTE_TYPE=int8`, and
  `WHISPER_DEVICE=cpu`: speech-to-text model and execution settings.
- `VOICE_MAX_PARALLEL=1`: maximum simultaneous Whisper transcriptions (CPU
  default). `VOICE_MAX_CONCURRENCY` is a legacy alias used only when
  `VOICE_MAX_PARALLEL` is unset.
- `VOICE_QUEUE_MAX=16`: maximum *waiting* transcription jobs (not counting
  active ones). When full, the parent gets a localized busy/retry reply and
  the job is not transcribed.
- `VOICE_QUEUE_NOTIFY_AT=2`: after a job is admitted, if the waiting count
  (waiting only) is at least this value, send one short localized
  "processing" status. Active transcriptions are not counted.
- `VOICE_MAX_SECONDS=30` and `VOICE_MAX_BYTES=5242880`: upload limits.
- `TTS_CACHE_SIZE=128`: process-local synthesized-audio LRU entries.
- `VOICE_QUEUE_TIMEOUT=2.0`: legacy; admission uses `VOICE_QUEUE_MAX` instead.
- `VOICE_GUARDIAN_RATE=10`: voice-chat requests per guardian per 60 seconds.
- `VOICE_GLOBAL_RATE=60`: all voice chat and speak requests per process per
  60 seconds.
- `VOICE_SPEAK_RATE=20`: `/voice/speak` requests per authenticated owner per
  60 seconds.
- `VOICE_TTS_TIMEOUT=4.0`: seconds per gTTS attempt; one retry is made.
- `VOICE_AUDIO_TTL=300.0`: generated chat audio lifetime in process memory.
- `VOICE_VAD_SILENCE_MS=800`: browser silence interval that ends a turn.
- `VOICE_IDLE_PROMPT_SECONDS=20`: silence before Vaani asks if the parent is
  still present.

Restart the service after changing environment variables.

## Voice transcription queue

Speech-to-text runs behind a process-local FIFO queue in each uvicorn worker:

1. A job is admitted if either an active STT slot is free
   (`active < VOICE_MAX_PARALLEL`) or there is waiting room
   (`waiting < VOICE_QUEUE_MAX`).
2. Waiting capacity does **not** include active transcriptions.
3. Admitted jobs run Whisper one-after-another up to `VOICE_MAX_PARALLEL`.
   Agent / TTS work happens after the STT slot is released.
4. If waiting jobs after admission are `>= VOICE_QUEUE_NOTIFY_AT`, at most one
   localized status phrase is sent via the configured notifier (HTTP responses
   also expose `queue_notice` when that fires). Messaging failures do not free
   or duplicate STT work.
5. If the waiting queue is full, the API returns HTTP 429 with a localized busy
   phrase; that audio is not transcribed.

The queue is not shared across workers or hosts. Run a single worker for the
workbook-backed demo, as elsewhere in this guide.

## Voice load test (issue #55)

Load-test the real HTTP API (`POST /voice/chat`) with concurrent multipart
uploads. Do not call Whisper in-process from the script.

### Prerequisites

1. App running with **one** uvicorn worker, `VOICE_ENABLED=true`, and
   `/voice/status` → `ready`.
2. Demo workbook guardians with consent (aliases `parent-a` … `parent-e`).
3. Approved clips in `tests/fixtures/voice_load/` (synthetic WAVs) or
   `--clips-dir` pointing at other authorized test audio — never real parents'
   recordings without authorization.
4. Optional: Docker Compose service `api` for container CPU/memory via
   `docker stats`.

### Commands

```powershell
# Preflight (no audio upload)
python -m app.voice.loadtest --concurrent 5 --dry-run

# Measured bursts (run separately; settle pause is built-in)
python -m app.voice.loadtest --concurrent 5 --warmup 1
python -m app.voice.loadtest --concurrent 10 --warmup 1
python -m app.voice.loadtest --concurrent 20 --warmup 1
```

Overrides: `--base-url`, `--endpoint` (default `/voice/chat`), `--timeout`,
`--output-dir`, `--docker-container`, `--clips-dir`, `--token` /
`LOADTEST_TOKEN`, `LOADTEST_BASE_URL`.

### Outputs

Each run writes **new** timestamped files under `benchmark_results/` (gitignored):

- `load-<utc>-cN-<run_id>.csv` — per-request timings
- `resources-<utc>-cN-<run_id>.csv` — container samples when Docker works
- `summary-<utc>-cN-<run_id>.json` — machine-readable summary

`queue_wait_seconds` is server `timings_ms.queue / 1000` when present; otherwise
`NA`. Client `total_seconds` is wall time and is never used as fake queue wait.

Decision write-up template: [docs/voice-load-test-report.md](docs/voice-load-test-report.md).

### Illustrative (not measured) reading of a summary

```text
Concurrency 5
  successes:     5
  median total:  2.4s          # example only
  avg queue wait: 0.8s (n=5)   # example only
```

Replace with values from your CSVs. GPU comparison requires a CUDA host; until
then mark it **NOT RUN** in the report.

## Voice startup and browser requirements

When voice is enabled, the API starts immediately and downloads/loads Whisper
in a background startup task. `/voice/status` reports `warming` until it is
ready. The first voice request never performs model loading: it receives 503
while warming. Text chat remains available.

Microphone capture requires a secure browser context. Browsers make an
exception for `http://localhost`, so local development works without TLS.
Remote deployments must use HTTPS and the user must grant microphone access.

The hands-free UI at `/flows/voice-assistant` prefers low-bitrate mono
WebM/Opus, then MP4/AAC for Safari, then Ogg/Opus. Browser and OS support
varies. The API also validates WAV and MP3 uploads.

## Manual checks

API check in PowerShell after startup:

```powershell
$base = "http://localhost:8000"
$token = (Invoke-RestMethod "$base/auth/demo-token").access_token
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod "$base/health"
Invoke-RestMethod "$base/voice/status" -Headers $headers
$contexts = Invoke-RestMethod "$base/guardians" -Headers $headers
$contexts | Format-Table id, student_id, name, consent_given_at
```

Choose a consented guardian/student from that output and upload a real file:

```powershell
$guardian = "<consented-guardian-id>"
$student = "<linked-student-id>"
curl.exe -sS -X POST "$base/voice/chat" `
  -H "Authorization: Bearer $token" `
  -F "guardian_id=$guardian" -F "student_id=$student" `
  -F "language=en" -F "session_id=manual-check" `
  -F "audio=@sample.webm;type=audio/webm"
```

UI check:

1. Open `http://localhost:8000/flows/voice-assistant`.
2. Confirm text chat works while voice is disabled or warming.
3. Tap **Start voice assistant** once and grant microphone access.
4. Confirm listening stops after speech plus about 800 ms silence, the answer
   plays automatically, and listening resumes without another tap.
5. Confirm captions, mute, stop/restart, idle prompt, barge-in, permission,
   offline, busy, no-consent, and text-only audio fallback states.
6. Repeat on a narrow phone viewport and at desktop width. Test at least
   Chrome/Edge and one Safari/Firefox target because recording formats differ.

## Native Render deployment

Use a native Python web service connected to this repository:

- Runtime: Python 3.11 (set Render's `PYTHON_VERSION` to a currently supported
  3.11 patch if the service does not already use 3.11).
- Build command: `python -m pip install -r requirements.txt`
- Start command:
  `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Add all required values from `.env.example` in Render's environment UI.
  Do not upload `.env`.
- Keep `SCHOOL_DATA_PATH=data/school_data.xlsx`. This remains the existing
  single-worker, workbook-based demo service.

For Render's 512 MB free plan, start with
`WHISPER_MODEL=tiny`, `WHISPER_COMPUTE_TYPE=int8`, and `WHISPER_DEVICE=cpu`
until memory is measured on that service. Reserve `base` for local/paid
instances unless a deployed measurement proves it fits.

Local Windows/Python 3.11 measurements on 11 October 2026, using the full
single-worker Uvicorn process, were:

- `tiny` int8 CPU: 245 MB working set after load, 332 MB peak.
- `base` int8 CPU: 288 MB working set after load, 426 MB peak; 313 MB after a
  real one-second silence transcription.

These are local measurements, not Render measurements. `base` leaves too
little safety margin on a 512 MB instance, so `tiny` remains the recommended
free-plan model until the deployed service is measured directly.

The Dockerfile prefetches its `WHISPER_MODEL` build argument (default `tiny`)
into the image. Native Render builds that only run `pip install` still
download during background application startup; `/voice/status` remains
`warming` meanwhile. A native build can prefetch explicitly:

```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')"
```

Keep build/runtime model names identical.

After deploy, use the API and UI checks above with
`$base = "https://YOUR-SERVICE.onrender.com"`. HTTPS is required for the remote
microphone. A free service may cold-start before voice warming begins.

## Architecture, security, and privacy

Voice uses the same tenant, guardian/student linkage, consent checks,
LangGraph orchestrator, and workbook conversation history as text chat.
Authorization and consent are checked before transcription. Incoming audio is
held in request/process memory only and is not written to disk by this app.

Generated chat audio is held in process memory for about five minutes by
default and is removed after one successful authorized download. The TTS LRU
cache is also process-local and bounded by `TTS_CACHE_SIZE`; all of it is lost
on restart. Rate limits, the global limit, one-job default concurrency, queue
timeout, file-size limit, and duration limit reduce demo abuse.

`/auth/demo-token` is only a development helper: its token identifies a tenant,
not an individual guardian. It is not production authentication. Production
must use real user identity, guardian-scoped authorization, HTTPS, protected
secrets, consent/audit controls, and a durable database.

## Known limits

- Whisper is not streaming, and gTTS returns finished MP3 data. Zero latency
  is not possible with these tools; browser VAD removes the manual stop delay.
- gTTS is an unofficial Google Translate client. It needs internet access and
  can fail or be rate-limited by the upstream service.
- Hindi recognition can be lower than English with small Whisper models.
- Model size is constrained by instance memory.
- Whisper, TTS cache, rate limits, and generated audio storage are
  process-local.
- Cold starts can include both Render startup and a model download/load.

Render memory, multilingual accuracy, and representative end-to-end latency
have not been measured. Only the local memory and silence-pipeline figures
above are claimed.

## Verification templates — NOT YET MEASURED

### Accuracy: 15 recordings in two languages

Prepare ten English recordings named `accuracy/en-01.webm` through
`en-10.webm` and five Hindi recordings `hi-01.webm` through `hi-05.webm`.
Create the result sheet before testing:

```powershell
'"environment,model,language,item,expected,transcript,pass,notes' |
  Set-Content accuracy-results.csv
```

For each file, run the upload command from the manual API check with the
matching `language`, copy the returned `transcript`, and append one CSV row.
Mark `pass` only when the school terms, names/numbers, and question intent are
usable. Record all 15 rows locally, then repeat against Render. Keep the raw
files and rubric fixed when comparing `tiny` and `base`.

Result placeholders:

```text
Local / model ______: NOT YET MEASURED (en __/10, hi __/5)
Render / model _____: NOT YET MEASURED (en __/10, hi __/5)
```

### Latency: 15 voice questions, p50/p95

Prepare representative files `perf/q01.webm` through `q15.webm`. Warm the
service first and confirm `/voice/status` is `ready`; note cold-start/model
warm time separately. This PowerShell loop captures the server's stage and
total timings:

```powershell
$rows = 1..15 | ForEach-Object {
  $file = "perf/q{0:d2}.webm" -f $_
  $json = curl.exe -sS -X POST "$base/voice/chat" `
    -H "Authorization: Bearer $token" `
    -F "guardian_id=$guardian" -F "student_id=$student" `
    -F "language=en" -F "session_id=perf-$($_)" `
    -F "audio=@$file;type=audio/webm" | ConvertFrom-Json
  $t = $json.timings_ms
  [pscustomobject]@{ item=$_; upload=$t.upload; decode=$t.decode; queue=$t.queue;
    stt=$t.stt; router=$t.router; agent_llm=$t.agent_llm; tts=$t.tts; total=$t.total }
}
$rows | Export-Csv latency-results.csv -NoTypeInformation
function Percentile($values, $p) {
  $sorted = @($values | Sort-Object)
  $sorted[[Math]::Max(0, [Math]::Ceiling($p * $sorted.Count) - 1)]
}
"p50_ms=$(Percentile $rows.total 0.50)"
"p95_ms=$(Percentile $rows.total 0.95)"
```

Run once locally and once on Render with the same 15 files and model. Also
save model, instance plan, date, failures, and whether the service was warm.

```text
Local / model ______ / p50 ____ ms / p95 ____ ms: NOT YET MEASURED
Render / model _____ / p50 ____ ms / p95 ____ ms: NOT YET MEASURED
```
