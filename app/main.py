from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.database import Base, SessionLocal, engine
from app.api import routes_chat, routes_students, routes_webhooks
from app.core.security import create_access_token
from app.config import settings

# Import models so metadata is fully registered before create_all.
from app.models import tenant, student, academic, conversation  # noqa: F401
from app.models.tenant import Tenant

# Dev convenience only - use Alembic migrations in production instead of
# create_all so schema changes are tracked and reversible.
Base.metadata.create_all(bind=engine)

UI_PATH = Path(__file__).resolve().parent.parent / "school_ai_platform_flow.html"

app = FastAPI(title="School Parent AI Platform")

app.include_router(routes_chat.router)
app.include_router(routes_students.router)
app.include_router(routes_webhooks.router)


@app.on_event("startup")
def seed_demo_tenant():
    db = SessionLocal()
    try:
        if not db.get(Tenant, settings.default_tenant_id):
            db.add(Tenant(id=settings.default_tenant_id, name="Demo School"))
            db.commit()
    finally:
        db.close()


@app.get("/")
def ui():
    """Serve the HTML application UI."""
    return FileResponse(UI_PATH, media_type="text/html")


@app.get("/auth/demo-token")
def demo_token():
    """Local-dev helper so the HTML UI can call authenticated routes."""
    return {
        "access_token": create_access_token({"tenant_id": settings.default_tenant_id}),
        "token_type": "bearer",
        "tenant_id": settings.default_tenant_id,
    }


@app.get("/health")
def health():
    return {"status": "ok"}
