from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

import logging

from app.api import routes_chat, routes_students, routes_webhooks, routes_ops, routes_teachers
from app.core.security import create_access_token
from app.services.excel_store import get_store

logger = logging.getLogger(__name__)

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
    "teacher-workspace",
}

app = FastAPI(title="School Parent AI Platform")

app.include_router(routes_chat.router)
app.include_router(routes_students.router)
app.include_router(routes_webhooks.router)
app.include_router(routes_ops.router)
app.include_router(routes_teachers.router)


@app.on_event("startup")
def check_workbook():
    """Fail fast if the workbook is missing or has no school in it."""
    store = get_store()
    logger.info("Using %s (school: %s)", store.path, store.default_tenant_id())


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
    store = get_store()
    tenant_id = store.default_tenant_id()
    tenant = store.get("tenants", tenant_id) or {}
    return {
        "access_token": create_access_token({"tenant_id": tenant_id}),
        "token_type": "bearer",
        "tenant_id": tenant_id,
        "school_name": tenant.get("name"),
        "whatsapp_phone_number_id": tenant.get("whatsapp_phone_number_id"),
    }


@app.get("/health")
def health():
    return {"status": "ok"}
