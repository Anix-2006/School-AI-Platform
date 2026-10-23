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

The browser voice demo is at http://localhost:8000/flows/voice (set
`VOICE_ENABLED=true`). Choose a parent, record a question, and the page plays
the spoken reply. Recording stops just before `VOICE_MAX_SECONDS` (30 seconds
unless that setting is changed). The spoken reply uses the parent's language
when it is English, Hindi, or Telugu. Any other language is spoken in English,
and the response reports English. Microphone access works on `http://localhost`.
Any other host needs HTTPS.

**Start conversation** on that page opens a live session at `/voice/live`.
Authentication, the guardian, and consent are checked once when the session
starts. The microphone stays open. WebRTC VAD (the `webrtcvad` module from
`webrtcvad-wheels`) splits the audio: a check with spoken English, Hindi, and
Telugu questions plus background noise produced three utterances at every
aggressiveness setting, so this lighter detector is used instead of Silero.
An utterance ends after `VOICE_VAD_SILENCE_MS` (800 ms unless changed) and
cannot run longer than `VOICE_MAX_SECONDS`. `VOICE_VAD_SENSITIVITY` is 0–3
(0 keeps quiet speech, 3 rejects more noise; default 2). Each utterance uses
the same voice flow as a single recording, on one session so the conversation
keeps its memory. Speaking while a reply is playing stops playback and cancels
that reply. Long replies are spoken sentence by sentence. Up to
`TTS_MAX_PARALLEL` sentences (default 3) are generated at once, each sentence
is cached on its own, and live playback starts with the first sentence as
soon as it is ready. A voice note still receives one MP3, joined in the
original order. To time that speech stage from the repo root (real gTTS, so
the machine needs network access):

    py -3.11 scripts/benchmark_tts_sentences.py

The script adds the repository root to its own import path.

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
