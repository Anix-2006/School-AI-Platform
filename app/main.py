from pathlib import Path
import asyncio
import math
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

import logging

from app.api import routes_chat, routes_students, routes_webhooks, routes_ops, routes_teachers, routes_voice
from app.config import settings
from app.core.security import create_access_token
from app.services.excel_store import get_store
from app.voice.runtime import VoiceRuntime

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
FLOW_PAGES = {
    "platform-overview",
    "parent-chat",
    "voice-assistant",
    "voice",
    "ask-a-helper",
    "whatsapp",
    "daily-updates",
    "risk-alerts",
    "school-records",
    "school-account",
    "teacher-workspace",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    store.ensure_columns("messages", ["student_id", "intent_source", "intent_confidence"])
    logger.info("Using %s (school: %s)", store.path, store.default_tenant_id())

    runtime = getattr(app.state, "voice_runtime", None)
    if runtime is None:
        runtime = VoiceRuntime()
        app.state.voice_runtime = runtime
    task = None
    warm = getattr(runtime, "warm", None)
    if warm is not None and runtime.state == "warming":
        task = asyncio.create_task(warm(), name="voice-runtime-warm")
        app.state.voice_warm_task = task
    try:
        yield
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        close = getattr(runtime, "close", None)
        if close is not None:
            close()
        app.state.voice_warm_task = None


app = FastAPI(title="School Parent AI Platform", lifespan=lifespan)

app.include_router(routes_chat.router)
app.include_router(routes_students.router)
app.include_router(routes_webhooks.router)
app.include_router(routes_ops.router)
app.include_router(routes_teachers.router)
app.include_router(routes_voice.router)
app.include_router(routes_voice.live_router)


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


@app.get("/voice-live-worklet.js")
def voice_live_worklet():
    return FileResponse(UI_DIR / "voice-live-worklet.js", media_type="application/javascript")


@app.get("/voice-mascot-{size}.png")
def voice_mascot(size: int):
    if size not in {32, 512}:
        raise HTTPException(status_code=404, detail="Mascot size not found")
    return FileResponse(UI_DIR / "assets" / f"vaani-{size}.png", media_type="image/png")


def _voice_limit_token(value: float) -> str:
    """Render VOICE_MAX_SECONDS for the demo page. Non-numeric values use the default."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 30.0
    if not math.isfinite(number) or number <= 0:
        number = 30.0
    return f"{number:g}"


@app.get("/flows/{page}")
def ui_flow_page(page: str):
    """Serve one of the flow-reference HTML pages."""
    if page not in FLOW_PAGES:
        raise HTTPException(status_code=404, detail="Flow page not found")
    path = UI_DIR / "flows" / f"{page}.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Flow page not found")
    if page == "voice":
        html = path.read_text(encoding="utf-8")
        html = html.replace("__VOICE_MAX_SECONDS__", _voice_limit_token(settings.voice_max_seconds))
        html = html.replace("__VOICE_SAMPLE_RATE__", str(int(settings.voice_vad_sample_rate)))
        return HTMLResponse(html)
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
