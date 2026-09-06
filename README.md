# School Parent AI Platform

Multi-agent AI system for CBSE schools (Pre-primary through Grade 7) that sends
parents daily updates, syllabus/academic progress, and rank cards over
WhatsApp/voice, backed by a LangGraph orchestrator running on OpenAI models.

## Architecture

Client (WhatsApp/voice/app) -> FastAPI gateway -> LangGraph orchestrator
-> specialist agent nodes (daily update, academic, communication, insight)
-> Postgres (tenant-isolated) + vector store (CBSE curriculum RAG).

See `app/agents/orchestrator.py` for the graph definition.

## Structure

```
app/
  main.py            FastAPI app entrypoint
  config.py          Settings (env-driven)
  database.py         SQLAlchemy session/engine
  models/            ORM models: tenant, student, academic, conversation
  schemas/           Pydantic request/response schemas
  agents/            LangGraph orchestrator + 4 specialist agents + tools
  services/          WhatsApp integration, curriculum RAG
  core/               Auth + multi-tenant isolation helpers
  api/                FastAPI routers (chat, students, webhooks)
  tasks/              Scheduled batch jobs (daily updates, rank cards)
tests/
```

## Setup

```bash
cp .env.example .env      # fill in OPENAI_API_KEY, DATABASE_URL, WHATSAPP_TOKEN
docker compose up --build
```

API docs at http://localhost:8000/docs once running.

## What's production-shaped vs stubbed

Real: agent graph, tool-calling contracts, tenant isolation pattern,
DB schema, WhatsApp webhook contract, async daily batch job shape.

Stubbed (intentionally, fill in for your deployment): actual WhatsApp
Business API credentials/templates, real CBSE curriculum content for the
RAG index (currently a placeholder loader), production auth provider
(a minimal JWT scheme is included), and CI/CD pipeline config.

## Next steps to harden for real production

1. Swap the in-memory/sqlite dev DB for managed Postgres with row-level
   security policies per tenant (see `app/core/tenancy.py` for the pattern
   to extend).
2. Wire `app/services/rag_service.py` to a real vector DB (pgvector,
   Pinecone, etc.) loaded with actual NCERT/CBSE syllabus content.
3. Replace the stub WhatsApp client with the real Business API + message
   templates approved by Meta.
4. Add DPDP-compliant consent capture before any student data is
   processed, and audit logging on every student record access.
5. Move `app/tasks/daily_batch.py` behind a real scheduler (Celery beat,
   or a cron-triggered K8s job) instead of the manual trigger endpoint.
