"""WhatsApp Business API client. Stubbed - fill in with real Meta Graph
API calls and approved message templates before production use."""

import httpx

from app.config import settings

GRAPH_API_URL = "https://graph.facebook.com/v20.0"


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
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
