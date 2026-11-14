"""Bounded, authenticated WhatsApp media download (HTTP fully mocked)."""

import asyncio

import httpx
import pytest

from app.config import settings
from app.services.whatsapp_service import GRAPH_API_URL, WhatsAppMediaError, download_whatsapp_media

MEDIA_ID = "1234567890"
MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1234567890"
TOKEN = "test-token"


class ChunkStream(httpx.AsyncByteStream):
    """A body served in chunks; counts how many were pulled."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.served = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.served += 1
            yield chunk


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", TOKEN)


def run(handler, max_bytes=1000, media_id=MEDIA_ID):
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(recording)) as client:
            return await download_whatsapp_media(media_id, max_bytes=max_bytes, client=client)

    try:
        return asyncio.run(go()), calls
    except WhatsAppMediaError as exc:
        return exc, calls


def graph(meta=None, status=200, media=None):
    meta = {"url": MEDIA_URL, "mime_type": "audio/ogg; codecs=opus", "id": MEDIA_ID} if meta is None else meta

    def handler(request):
        if str(request.url) == f"{GRAPH_API_URL}/{MEDIA_ID}":
            return httpx.Response(status, json=meta)
        if str(request.url) == MEDIA_URL and media is not None:
            return media(request)
        return httpx.Response(599)

    return handler


def test_metadata_then_authenticated_download():
    media, calls = run(graph(media=lambda r: httpx.Response(200, content=b"OggS-audio")))
    assert media.data == b"OggS-audio"
    assert media.mime_type == "audio/ogg; codecs=opus"
    assert [str(c.url) for c in calls] == [f"{GRAPH_API_URL}/{MEDIA_ID}", MEDIA_URL]
    assert all(c.headers["authorization"] == f"Bearer {TOKEN}" for c in calls)


def test_missing_token_is_a_controlled_error(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "")
    error, calls = run(graph())
    assert isinstance(error, WhatsAppMediaError) and error.reason == "unavailable"
    assert "WHATSAPP_TOKEN" in error.detail
    assert calls == []


@pytest.mark.parametrize("media_id", ["", "../me", "123/messages", "a b"])
def test_invalid_media_id_makes_no_request(media_id):
    error, calls = run(graph(), media_id=media_id)
    assert error.reason == "unavailable" and calls == []


@pytest.mark.parametrize("meta", [{}, {"url": None}, {"url": 42}, ["not", "a", "dict"]])
def test_missing_media_url(meta):
    error, calls = run(graph(meta=meta))
    assert error.reason == "unavailable"
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 404, 500])
def test_metadata_http_error(status):
    error, calls = run(graph(status=status))
    assert error.reason == "unavailable" and str(status) in error.detail
    assert len(calls) == 1


def test_media_http_error():
    error, _ = run(graph(media=lambda r: httpx.Response(403)))
    assert error.reason == "unavailable" and "403" in error.detail


def test_network_timeout():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    error, _ = run(handler)
    assert error.reason == "unavailable" and "ConnectTimeout" in error.detail
    assert TOKEN not in error.detail


@pytest.mark.parametrize("url", [
    "http://lookaside.fbsbx.com/media",          # not HTTPS
    "https://evil.example.com/media",            # untrusted host
    "https://fbsbx.com.evil.example/media",      # suffix trick
    "https://user:pw@lookaside.fbsbx.com/media",  # credentials in URL
    "file:///etc/passwd",
])
def test_untrusted_media_url_never_gets_the_token(url):
    error, calls = run(graph(meta={"url": url, "mime_type": "audio/ogg"}))
    assert error.reason == "unavailable"
    assert len(calls) == 1  # only the Graph metadata request


def test_redirect_is_not_followed_with_the_token():
    def redirect(request):
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    error, calls = run(graph(media=redirect))
    assert error.reason == "unavailable" and "302" in error.detail
    assert all("evil.example.com" not in str(c.url) for c in calls)


def test_declared_file_size_over_limit_skips_download():
    meta = {"url": MEDIA_URL, "mime_type": "audio/ogg", "file_size": 5000}
    error, calls = run(graph(meta=meta), max_bytes=1000)
    assert error.reason == "too_large" and len(calls) == 1


def test_content_length_over_limit_stops_before_reading_body():
    body = ChunkStream([b"x" * 500] * 100)

    def media(request):
        return httpx.Response(200, headers={"content-length": "50000"}, stream=body)

    error, _ = run(graph(media=media), max_bytes=1000)
    assert error.reason == "too_large" and body.served == 0


@pytest.mark.parametrize("headers", [{}, {"content-length": "10"}])
def test_missing_or_lying_content_length_cannot_bypass_the_limit(headers):
    body = ChunkStream([b"x" * 500] * 100)

    def media(request):
        return httpx.Response(200, headers=headers, stream=body)

    error, _ = run(graph(media=media), max_bytes=1000)
    assert error.reason == "too_large"
    assert body.served <= 3  # stopped just past 1000 bytes, not after 50 KB
