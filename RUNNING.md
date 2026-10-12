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
- `WHISPER_MODEL=small`, `WHISPER_COMPUTE_TYPE=int8`, and
  `WHISPER_DEVICE=cpu`: speech-to-text model and execution settings.
- `WHISPER_MODEL_DIR=models/whisper`: where
  `python -m app.voice.download_model` stores the model and where the app
  loads it from (see **Voice setup** in [README.md](README.md)).
- `VOICE_MAX_CONCURRENCY=1`: simultaneous speech-to-text/chat voice jobs.
- `VOICE_MAX_SECONDS=30` and `VOICE_MAX_BYTES=5242880`: upload limits.
- `TTS_CACHE_SIZE=128`: process-local synthesized-audio LRU entries.
- `VOICE_QUEUE_TIMEOUT=2.0`: seconds to wait for a voice job slot.
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

## Voice startup and browser requirements

When voice is enabled, the API starts immediately and loads Whisper from
`WHISPER_MODEL_DIR` in a background startup task. It never downloads the model;
run `python -m app.voice.download_model` first, otherwise `/voice/status`
reports `error`. `/voice/status` reports `warming` until the model is ready. The first voice request never performs model loading: it receives 503
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
- Build command:
  `python -m pip install -r requirements.txt && python -m app.voice.download_model`
  (the download step needs the voice settings below and `OPENAI_API_KEY` to be
  available at build time).
- Start command:
  `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Add all required values from `.env.example` in Render's environment UI.
  Do not upload `.env`.
- Keep `SCHOOL_DATA_PATH=data/school_data.xlsx`. This remains the existing
  single-worker, workbook-based demo service.

For Render's 512 MB free plan, set
`WHISPER_MODEL=tiny`, `WHISPER_COMPUTE_TYPE=int8`, and `WHISPER_DEVICE=cpu`
explicitly (the code default is now `small`) until memory is measured on that
service. Reserve `base` for local/paid
instances unless a deployed measurement proves it fits.

Local Windows/Python 3.11 measurements on 11 October 2026, using the full
single-worker Uvicorn process, were:

- `tiny` int8 CPU: 245 MB working set after load, 332 MB peak.
- `base` int8 CPU: 288 MB working set after load, 426 MB peak; 313 MB after a
  real one-second silence transcription.

These are local measurements, not Render measurements. `base` leaves too
little safety margin on a 512 MB instance, so `tiny` remains the recommended
free-plan model until the deployed service is measured directly.

Neither the Dockerfile nor application startup downloads the model. The build
command above stores it under `WHISPER_MODEL_DIR`; a build that only runs
`pip install` leaves voice in the `error` state. Keep build/runtime model
names and `WHISPER_MODEL_DIR` identical.

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
- Cold starts include both Render startup and loading the model from disk.

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
