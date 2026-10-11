"""WhatsApp Business API client. Stubbed - fill in with real Meta Graph
API calls and approved message templates before production use."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v20.0"


async def download_whatsapp_media(media_id: str) -> tuple[bytes, str]:
    if not settings.whatsapp_token:
        raise RuntimeError("WhatsApp media download is not configured")

    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    async with httpx.AsyncClient() as client:
        meta = await client.get(f"{GRAPH_API_URL}/{media_id}", headers=headers)
        meta.raise_for_status()
        payload = meta.json()
        url = payload["url"]
        mime_type = str(payload.get("mime_type") or "audio/ogg")
        audio = await client.get(url, headers=headers)
        audio.raise_for_status()
        return audio.content, mime_type


async def send_whatsapp_message(to: str, body: str) -> dict:
    if not settings.whatsapp_token:
        # Dev mode - just log instead of calling the real API.
        print(f"[whatsapp-stub] to={to}: {body}")
        return {"status": "stubbed"}

    url = f"{GRAPH_API_URL}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp send to %s failed: %s", to, exc)
        return {"status": "failed", "error": str(exc)}
