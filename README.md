# School Parent AI Platform

Multi-agent AI system for CBSE schools (Pre-primary through Grade 7) that sends
parents daily updates, syllabus/academic progress, and rank cards over
WhatsApp/voice, backed by a LangGraph orchestrator running on OpenAI models.

## Architecture -

Client (WhatsApp/voice/app) -> FastAPI gateway -> LangGraph orchestrator
-> specialist agent nodes (daily update, academic, communication, insight)
-> demo data in `data/school_data.xlsx` (one sheet per table, tenant-isolated).

See `app/agents/orchestrator.py` for the graph definition.

Voice adds in-memory audio validation, faster-whisper speech-to-text, the same
chat access checks/orchestrator/history, and gTTS speech output. It is disabled
by default.

## Structure

```
app/
  main.py            FastAPI app entrypoint
  config.py          Settings (env-driven)
  schemas/           Pydantic request/response schemas
  agents/            LangGraph orchestrator + 4 specialist agents + tools
  services/          Excel store (get_store), chat, teacher workspace, WhatsApp, curriculum search
  core/               Auth + multi-tenant isolation helpers
  api/                FastAPI routers (chat, students, webhooks)
  tasks/              Scheduled batch jobs (daily updates, insight scan)
data/
  school_data.xlsx    All demo data: students, guardians, teachers, attendance, ...
tests/
```

## Setup

```bash
cp .env.example .env      # fill in OPENAI_API_KEY, WHATSAPP_TOKEN
docker compose up --build
```

API docs at http://localhost:8000/docs once running.

For the Python 3.11 setup, voice configuration, secure-browser requirements,
manual checks, Render deployment, limits, and unmeasured verification
templates, see [RUNNING.md](RUNNING.md). Run one uvicorn worker.

## Voice setup

Voice uses three tools: **ffmpeg** (audio tooling), **faster-whisper**
(speech-to-text) and **gTTS** (text-to-speech). Voice stays off until
`VOICE_ENABLED=true` in `.env`; text chat works without any of this.

### 1. Prerequisites (Windows)

- Python **3.11** (the version the Dockerfile and Render use).
- Open PowerShell in the repository folder: in File Explorer, open the
  `School-AI-Platform` folder, click the address bar, type `powershell`, and
  press Enter. Or run `cd D:\path\to\School-AI-Platform`.
- A virtual environment is recommended so these packages don't change your
  global Python:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation is blocked by the execution policy, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the same window
and activate again. Activate `.venv` in every new terminal before the commands
below.

### 2. Install ffmpeg

Use **one** of these:

```powershell
winget install Gyan.FFmpeg
```

```powershell
choco install ffmpeg
```

`winget` ships with current Windows 10/11. `choco` needs Chocolatey installed
and usually an Administrator PowerShell. Close and reopen PowerShell so the new
`PATH` is picked up, then check:

```powershell
ffmpeg -version
```

If you see "not recognized", run `where.exe ffmpeg`. If it prints nothing, add
the ffmpeg `bin` folder (the one containing `ffmpeg.exe`) to your user `PATH`,
reopen PowerShell, and try again.

### 3. Install Python dependencies

With `.venv` active:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import faster_whisper, gtts; print('voice imports OK')"
```

`requirements.txt` pins `faster-whisper==1.1.1` and `gTTS==2.5.4`.

### 4. Configure and download the Whisper model

Copy `.env.example` to `.env` if you haven't (`Copy-Item .env.example .env`)
and set `OPENAI_API_KEY`. The voice settings and their defaults:

| Setting | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `small` | faster-whisper model size (`tiny`, `base`, `small`, ...) |
| `WHISPER_DEVICE` | `cpu` | Where speech-to-text runs |
| `WHISPER_COMPUTE_TYPE` | `int8` | Precision; `int8` is the CPU-friendly choice |
| `WHISPER_MODEL_DIR` | `models/whisper` | Where the model is stored, relative to the repo root |

Download the model once, explicitly:

```powershell
python -m app.voice.download_model
```

The first run needs internet access. The `small` model can take several
hundred MB of download and disk space; bigger models need more. Files are
stored under `models/whisper` (gitignored). Running the command again reuses
the files already there instead of downloading them again. The app never
downloads a model at startup: if voice is enabled and the model is missing,
`/voice/status` reports `error` and the log says to run this command.

Then start the app with `VOICE_ENABLED=true` in `.env`:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

### 5. Docker

```powershell
docker compose up --build
```

The image installs ffmpeg and the Python packages, but does **not** contain the
Whisper model. The Compose service is `api`. Check the tools inside the running
container:

```powershell
docker compose exec api ffmpeg -version
docker compose exec api python -c "import faster_whisper, gtts; print('voice imports OK')"
```

The model lives in the named volume `whisper-models`, mounted at
`/app/models/whisper` (the default `WHISPER_MODEL_DIR`). It survives
`docker compose down` and image rebuilds; `docker volume ls` shows it with the
project name in front. Download into it once, then restart the service:

```powershell
docker compose exec api python -m app.voice.download_model
docker compose restart api
```

Don't add the download to the Dockerfile or run it on every rebuild. Keep
`WHISPER_MODEL_DIR=models/whisper` in `.env` when using Docker so it matches the
volume. Only `docker compose down -v` deletes the volume (and the model).

### 6. Health check and troubleshooting

```powershell
Invoke-RestMethod http://localhost:8000/health
```

`ffmpeg: True` means the `ffmpeg` executable is on the server's `PATH`;
`ffmpeg: False` means it isn't. This field is informational: `status` stays
`ok`, and the current voice code decodes audio through PyAV, which bundles its
own FFmpeg libraries.

- **`ffmpeg` not found:** reopen the terminal after installing, then check
  `where.exe ffmpeg` and your `PATH` (step 2). In Docker, rebuild with
  `docker compose up --build`.
- **Import errors** (`No module named faster_whisper` / `gtts`): activate
  `.venv`, confirm `python --version` is 3.11, and rerun
  `python -m pip install -r requirements.txt`.
- **No network during download:** the download fails with a nonzero exit code
  and a message. Connect and rerun `python -m app.voice.download_model`. A
  model already in `WHISPER_MODEL_DIR` keeps loading offline.
- **Disk space or permissions:** free space on the drive holding
  `WHISPER_MODEL_DIR`, or point `WHISPER_MODEL_DIR` at a folder you can write
  to (outside Docker).
- **CPU vs GPU:** this project is set up and tested only for
  `WHISPER_DEVICE=cpu` with `WHISPER_COMPUTE_TYPE=int8`. GPU (`cuda`) needs an
  NVIDIA GPU with matching CUDA/cuDNN libraries, which neither the Docker image
  nor this README configures.
- **Low-memory hosts:** `small` needs more memory than `tiny`. On Render's
  512 MB plan set `WHISPER_MODEL=tiny` explicitly (see
  [RUNNING.md](RUNNING.md)).

## Demo data

Every read and write goes through `get_store()` in
`app/services/excel_store.py`, which loads `data/school_data.xlsx`.
Edit the workbook and save it - the app picks up the change on the next
request, no restart needed. Close the file in Excel before using pages
that write (marking tasks done, adding students, chat history), because
Windows locks files that are open in Excel. Run a single uvicorn worker.

To export saved parent–teacher question/answer pairs for offline evaluation, from the repo root:

    python scripts/export_parent_teacher_turns.py

`--since YYYY-MM-DD` keeps turns on or after that date (inclusive). `--agent NAME` exports a different agent's replies (default `parent_teacher`). The file is written to `data/exports/parent_teacher_turns.jsonl`. Exports can contain sensitive student information, stay on this machine, and `data/exports/` is gitignored.

## What's production-shaped vs stubbed

Real: agent graph, tool-calling contracts, tenant isolation pattern,
data model (workbook sheets), WhatsApp webhook contract, async daily batch job shape.

Stubbed (intentionally, fill in for your deployment): actual WhatsApp
Business API credentials/templates, real CBSE curriculum content for the
RAG index (currently a placeholder loader), production auth provider
(a minimal JWT scheme is included), and CI/CD pipeline config.

## Next steps to harden for real production

1. Replace the Excel workbook with a managed database with row-level
   security policies per tenant (see `app/core/tenancy.py` and keep the
   `get_store()` interface so callers don't change).
2. Wire `app/services/rag_service.py` to a real vector DB (pgvector,
   Pinecone, etc.) loaded with actual NCERT/CBSE syllabus content.
3. Replace the stub WhatsApp client with the real Business API + message
   templates approved by Meta.
4. Add DPDP-compliant consent capture before any student data is
   processed, and audit logging on every student record access.
5. Move `app/tasks/daily_batch.py` behind a real scheduler (Celery beat,
   or a cron-triggered K8s job) instead of the manual trigger endpoint.
