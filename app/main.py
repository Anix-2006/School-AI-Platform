from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.database import Base, SessionLocal, engine
from app.api import routes_chat, routes_students, routes_webhooks, routes_ops
from app.core.security import create_access_token
from app.config import settings

# Import models so metadata is fully registered before create_all.
from app.models import tenant, student, academic, conversation  # noqa: F401
from app.models.tenant import Tenant

# Dev convenience only - use Alembic migrations in production instead of
# create_all so schema changes are tracked and reversible.
Base.metadata.create_all(bind=engine)

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
FLOW_PAGES = {
    "platform-overview",
    "parent-chat",
    "ask-a-helper",
    "whatsapp",
    "daily-updates",
    "risk-alerts",
    "school-records",
    "school-account",
}

app = FastAPI(title="School Parent AI Platform")

app.include_router(routes_chat.router)
app.include_router(routes_students.router)
app.include_router(routes_webhooks.router)
app.include_router(routes_ops.router)


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
def ui_home():
    """Main interactive UI (students + chat)."""
    return FileResponse(UI_DIR / "index.html", media_type="text/html")


@app.get("/styles.css")
def ui_styles():
    return FileResponse(UI_DIR / "styles.css", media_type="text/css")


@app.get("/sidebar.js")
def ui_sidebar_js():
    return FileResponse(UI_DIR / "sidebar.js", media_type="application/javascript")


@app.get("/api.js")
def ui_api_js():
    return FileResponse(UI_DIR / "api.js", media_type="application/javascript")


@app.get("/nav.js")
def ui_nav_js():
    return FileResponse(UI_DIR / "nav.js", media_type="application/javascript")


@app.get("/flows/{page}")
def ui_flow_page(page: str):
    """Serve one of the flow-reference HTML pages."""
    if page not in FLOW_PAGES:
        raise HTTPException(status_code=404, detail="Flow page not found")
    path = UI_DIR / "flows" / f"{page}.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Flow page not found")
    return FileResponse(path, media_type="text/html")


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
