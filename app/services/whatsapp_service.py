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

MEDIA_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MEDIA_DEADLINE_SECONDS = 45.0
# Media URLs from GET /{media_id} are served by Meta's CDN; the bearer token
# is only ever sent to these hosts over HTTPS.
TRUSTED_MEDIA_HOSTS = ("fbsbx.com", "facebook.com", "whatsapp.net")
_MEDIA_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class WhatsAppMediaError(Exception):
    """``reason`` is "too_large" or "unavailable"; ``detail`` is for logs only."""

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


async def download_whatsapp_media(
    media_id: str, *, max_bytes: int, client: httpx.AsyncClient | None = None
) -> WhatsAppMedia:
    """Fetch an inbound media file: GET /{media_id} for its URL, then download
    it with the bearer token, never buffering more than ``max_bytes``."""
    if not settings.whatsapp_token:
        raise WhatsAppMediaError("unavailable", "WHATSAPP_TOKEN is not set; media download is unavailable")
    if not _MEDIA_ID.match(media_id or ""):
        raise WhatsAppMediaError("unavailable", "invalid media id")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=MEDIA_TIMEOUT, follow_redirects=False)
    try:
        return await asyncio.wait_for(
            _download(client, media_id, max_bytes), timeout=MEDIA_DEADLINE_SECONDS
        )
    except asyncio.TimeoutError:
        raise WhatsAppMediaError("unavailable", "media download timed out") from None
    except httpx.HTTPError as exc:
        raise WhatsAppMediaError("unavailable", f"media request failed: {type(exc).__name__}") from None
    finally:
        if owns_client:
            await client.aclose()


async def _download(client: httpx.AsyncClient, media_id: str, max_bytes: int) -> WhatsAppMedia:
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    meta = await client.get(
        f"{GRAPH_API_URL}/{media_id}", headers=headers, timeout=MEDIA_TIMEOUT, follow_redirects=False
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
    if isinstance(declared_size, int) and declared_size > max_bytes:
        raise WhatsAppMediaError("too_large", "media file_size exceeds the limit")

    async with client.stream(
        "GET", url, headers=headers, timeout=MEDIA_TIMEOUT, follow_redirects=False
    ) as response:
        if response.status_code != 200:
            # Includes 3xx: redirects are never followed with the token attached.
            raise WhatsAppMediaError("unavailable", f"media download returned HTTP {response.status_code}")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > max_bytes:
            raise WhatsAppMediaError("too_large", "media content-length exceeds the limit")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise WhatsAppMediaError("too_large", "media download exceeded the limit")
        mime_type = str(info.get("mime_type") or response.headers.get("content-type") or "")
    return WhatsAppMedia(bytes(body), mime_type)


def _is_trusted_media_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    return any(host == trusted or host.endswith("." + trusted) for trusted in TRUSTED_MEDIA_HOSTS)
