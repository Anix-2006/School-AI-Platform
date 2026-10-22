"""WhatsApp Business API client. Stubbed - fill in with real Meta Graph
API calls and approved message templates before production use."""

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v20.0"
MEDIA_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
MEDIA_DEADLINE_SECONDS = 45.0
# GET /{media_id} returns a CDN URL. The bearer token is only sent to these hosts.
TRUSTED_MEDIA_HOSTS = ("fbsbx.com", "facebook.com", "whatsapp.net")
_MEDIA_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class WhatsAppMediaError(Exception):
    """``reason`` is ``too_large`` or ``unavailable``. ``detail`` is for logs only."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class WhatsAppMedia:
    data: bytes
    mime_type: str


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


async def upload_media(data: bytes, mime_type: str) -> dict:
    """Upload reply audio. A successful Meta upload includes ``media_id``.

    Without a token, logs and returns ``{"status": "stubbed"}``. That is not a
    delivery, and no media id is invented. Failures are logged and returned.
    """
    if not data:
        logger.warning("WhatsApp media upload skipped: empty audio")
        return {"status": "failed", "error": "empty audio"}
    if not settings.whatsapp_token:
        print(f"[whatsapp-stub] upload mime={mime_type} bytes={len(data)}")
        return {"status": "stubbed"}

    url = f"{GRAPH_API_URL}/{settings.whatsapp_phone_number_id}/media"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    files = {"file": ("voice.ogg", data, mime_type)}
    form = {"messaging_product": "whatsapp", "type": mime_type}
    try:
        async with httpx.AsyncClient(timeout=MEDIA_TIMEOUT) as client:
            resp = await client.post(url, data=form, files=files, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("WhatsApp media upload failed: %s", exc)
        return {"status": "failed", "error": str(exc)}
    media_id = body.get("id") if isinstance(body, dict) else None
    if not media_id:
        logger.warning("WhatsApp media upload returned no id")
        return {"status": "failed", "error": "missing media id"}
    return {"status": "uploaded", "media_id": str(media_id)}


async def send_whatsapp_audio(to: str, media_id: str | None = None) -> dict:
    """Send an uploaded OGG/Opus file as a voice note. Failures are logged, not raised.

    Without a token, logs the attempt and returns ``{"status": "stubbed"}``.
    A missing media id is only an error when a real send was requested.
    """
    if not settings.whatsapp_token:
        print(f"[whatsapp-stub] audio to={to}")
        return {"status": "stubbed"}
    if not media_id:
        logger.warning("WhatsApp audio to %s skipped: missing media id", to)
        return {"status": "failed", "error": "missing media id"}

    url = f"{GRAPH_API_URL}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id, "voice": True},
    }
    try:
        async with httpx.AsyncClient(timeout=MEDIA_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("WhatsApp audio to %s failed: %s", to, exc)
        return {"status": "failed", "error": str(exc)}
    if isinstance(body, dict):
        return body
    return {"status": "sent"}


async def download_whatsapp_media(
    media_id: str, *, max_bytes: int, client: httpx.AsyncClient | None = None
) -> WhatsAppMedia:
    """Fetch an inbound voice note: metadata first, then the file, never past ``max_bytes``."""
    if not settings.whatsapp_token:
        raise WhatsAppMediaError(
            "unavailable", "WHATSAPP_TOKEN is not set; media download is unavailable"
        )
    if not _MEDIA_ID.match(media_id or ""):
        raise WhatsAppMediaError("unavailable", "invalid media id")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=MEDIA_TIMEOUT, follow_redirects=False)
    try:
        return await asyncio.wait_for(
            _download_media(client, media_id, max_bytes), timeout=MEDIA_DEADLINE_SECONDS
        )
    except WhatsAppMediaError:
        raise
    except asyncio.TimeoutError:
        raise WhatsAppMediaError("unavailable", "media download timed out") from None
    except httpx.HTTPError as exc:
        raise WhatsAppMediaError("unavailable", f"media request failed: {type(exc).__name__}") from None
    finally:
        if owns_client:
            await client.aclose()


async def _download_media(client: httpx.AsyncClient, media_id: str, max_bytes: int) -> WhatsAppMedia:
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    meta = await client.get(
        f"{GRAPH_API_URL}/{media_id}",
        headers=headers,
        timeout=MEDIA_TIMEOUT,
        follow_redirects=False,
    )
    if meta.status_code != 200:
        raise WhatsAppMediaError("unavailable", f"media metadata returned HTTP {meta.status_code}")
    try:
        info = meta.json()
    except ValueError:
        raise WhatsAppMediaError("unavailable", "media metadata is not JSON") from None
    url = info.get("url") if isinstance(info, dict) else None
    if not isinstance(url, str) or not _is_trusted_media_url(url):
        raise WhatsAppMediaError("unavailable", "media metadata has no trusted HTTPS url")
    declared_size = info.get("file_size")
    if isinstance(declared_size, int) and not isinstance(declared_size, bool) and declared_size > max_bytes:
        raise WhatsAppMediaError("too_large", "media file_size exceeds the limit")
    mime_type = str(info.get("mime_type") or "")

    async with client.stream(
        "GET", url, headers=headers, timeout=MEDIA_TIMEOUT, follow_redirects=False
    ) as response:
        if response.status_code != 200:
            raise WhatsAppMediaError("unavailable", f"media download returned HTTP {response.status_code}")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > max_bytes:
            raise WhatsAppMediaError("too_large", "media content-length exceeds the limit")
        if not mime_type:
            mime_type = str(response.headers.get("content-type") or "")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise WhatsAppMediaError("too_large", "media download exceeded the limit")
    return WhatsAppMedia(bytes(body), mime_type)


def _is_trusted_media_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    return any(host == trusted or host.endswith("." + trusted) for trusted in TRUSTED_MEDIA_HOSTS)
