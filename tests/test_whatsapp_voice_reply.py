"""WhatsApp voice notes: text reply first, then an OGG/Opus voice note."""

import asyncio
import io
import json
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import httpx
import pytest

from app.api import routes_webhooks
from app.config import settings
from app.services import chat_service
from app.services.whatsapp_service import (
    WhatsAppMedia,
    WhatsAppMediaError,
    download_whatsapp_media,
    send_whatsapp_audio,
    upload_media,
)
from app.voice.audio import AudioConversionError, _opus_command, mp3_to_ogg_opus
from app.voice.service import VoiceService
from app.voice.storage import AudioStore
from app.voice.stt import Transcript
from tests.fakes import FakeGraph

SURESH = "919000000005"  # father of Rohan, consent given
MEENA = "919000000006"  # mother of Vikram, no consent
REPLY = "**Rohan** was absent on 6 October."


class FakeStt:
    def __init__(self, text="Was my son present?", language="en"):
        self.result = Transcript(text, language, 1.0, 1.0)
        self.calls = []

    def transcribe(self, audio, content_type, language=None, initial_prompt=None):
        self.calls.append((audio, content_type, language, initial_prompt))
        return self.result


class FakeTts:
    def __init__(self, fail=False, audio=b"ID3speech"):
        self.fail = fail
        self.audio = audio
        self.calls = []

    async def synthesize(self, text, language):
        self.calls.append((text, language))
        if self.fail:
            raise RuntimeError("Text-to-speech is temporarily unavailable")
        return self.audio


class RecordingStore(AudioStore):
    def __init__(self):
        super().__init__()
        self.saved = []

    def put(self, owner_id, data):
        self.saved.append((owner_id, data))
        return super().put(owner_id, data)


class FakeRuntime:
    state = "ready"

    def __init__(self, service):
        self.service = service


def _wav(seconds=0.25) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x01" * int(8000 * seconds))
    return target.getvalue()


def _ffmpeg():
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.fail("ffmpeg is required to verify MP3 to OGG/Opus conversion")
    return binary


def _mp3() -> bytes:
    result = subprocess.run(
        [
            _ffmpeg(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=_wav(),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and result.stdout, result.stderr
    return result.stdout


def _opus_layout(ogg: bytes) -> tuple[int, int]:
    start = ogg.index(b"OpusHead")
    channels = ogg[start + 9]
    rate = int.from_bytes(ogg[start + 12 : start + 16], "little")
    return channels, rate


def test_mp3_to_ogg_opus_produces_valid_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    mp3 = _mp3()
    ogg = mp3_to_ogg_opus(mp3)

    assert ogg.startswith(b"OggS")
    channels, rate = _opus_layout(ogg)
    assert channels == 1
    assert rate == 16000
    command = _opus_command("ffmpeg", "in.mp3", "out.ogg")
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-b:a") + 1] == "24k"
    ffprobe = shutil.which("ffprobe")
    assert ffprobe, "ffprobe is required to check the Opus voice note"
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,channels",
            "-of",
            "json",
            "-i",
            "pipe:0",
        ],
        input=ogg,
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "opus"
    assert int(stream["channels"]) == 1
    decoded = subprocess.run(
        [
            _ffmpeg(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "pipe:1",
        ],
        input=ogg,
        capture_output=True,
        check=False,
    )
    assert decoded.returncode == 0, decoded.stderr
    assert len(decoded.stdout) > 100
    assert list(Path(tmp_path).rglob("*.ogg")) == []
    assert list(Path(tmp_path).rglob("*.mp3")) == []


def test_empty_or_undecodable_audio_raises_conversion_error(monkeypatch):
    with pytest.raises(AudioConversionError):
        mp3_to_ogg_opus(b"")
    with pytest.raises(AudioConversionError):
        mp3_to_ogg_opus(b"ID3not-audio")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(AudioConversionError):
        mp3_to_ogg_opus(b"ID3not-audio")


def _phone(store):
    return store.rows("tenants")[0]["whatsapp_phone_number_id"]


def _payload(store, from_number, *, kind="audio", media_id="media-1", mime="audio/ogg; codecs=opus", text="Hello"):
    message = {"from": from_number, "type": kind}
    if kind == "audio":
        message["audio"] = {"id": media_id, "mime_type": mime, "voice": True}
    else:
        message["text"] = {"body": text}
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": _phone(store)},
        "messages": [message],
    }}]}]}


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setattr(settings, "env", "development")


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph(reply=REPLY)
    monkeypatch.setattr(chat_service, "orchestrator_graph", fake)
    return fake


@pytest.fixture
def voice_box(client):
    from app.main import app

    previous = getattr(app.state, "voice_runtime", None)
    box = {"stt": FakeStt(), "tts": FakeTts(), "store": RecordingStore(), "events": []}
    app.state.voice_runtime = FakeRuntime(
        VoiceService(box["stt"], box["tts"], box["store"])
    )
    yield box
    app.state.voice_runtime = previous


def _install_delivery(monkeypatch, events, *, upload_result=None, audio_result=None, convert=True):
    async def fake_download(media_id, *, max_bytes, client=None):
        events.append(("download", media_id))
        return WhatsAppMedia(b"OggS-inbound", "audio/ogg")

    async def fake_text(to, body):
        events.append(("text", to, body))
        return {"status": "stubbed"}

    def fake_convert(mp3):
        events.append(("convert", mp3))
        if not convert:
            raise AudioConversionError("ffmpeg failed")
        return b"OggS" + b"OpusHead" + b"reply"

    async def fake_upload(data, mime_type):
        events.append(("upload", mime_type, data[:4]))
        if upload_result is not None:
            return upload_result
        return {"status": "uploaded", "media_id": "media-42"}

    async def fake_audio(to, media_id):
        events.append(("audio", to, media_id))
        if audio_result is not None:
            return audio_result
        return {"status": "stubbed"}

    monkeypatch.setattr(routes_webhooks, "download_whatsapp_media", fake_download)
    monkeypatch.setattr(routes_webhooks, "send_whatsapp_message", fake_text)
    monkeypatch.setattr(routes_webhooks, "mp3_to_ogg_opus", fake_convert)
    monkeypatch.setattr(routes_webhooks, "upload_media", fake_upload)
    monkeypatch.setattr(routes_webhooks, "send_whatsapp_audio", fake_audio)


def _kinds(events):
    return [item[0] for item in events]


def test_voice_note_sends_text_then_audio(client, store, graph, voice_box, dev_env, monkeypatch):
    events = voice_box["events"]
    _install_delivery(monkeypatch, events)

    response = client.post("/webhooks/whatsapp", json=_payload(store, SURESH))

    body = response.json()
    assert body["status"] == "ok"
    assert body["reply"] == REPLY
    assert body["audio_produced"] is True
    assert _kinds(events) == ["download", "text", "convert", "upload", "audio"]
    assert events[1] == ("text", SURESH, REPLY)
    assert events[2][1] == b"ID3speech"
    assert events[3][1] == "audio/ogg; codecs=opus"
    assert events[4] == ("audio", SURESH, "media-42")
    assert voice_box["tts"].calls
    assert voice_box["store"].saved == []
    assert graph.calls[0]["config"]["configurable"]["thread_id"] == "g-5-1:whatsapp"
    assert graph.calls[0]["state"]["channel"] == "chat"
    conversation = store.find("conversations", guardian_id="g-5-1")[0]
    assert conversation["channel"] == "whatsapp"
    assert voice_box["stt"].calls[0][0] == b"OggS-inbound"


def test_tts_failure_sends_text_only(client, store, graph, voice_box, dev_env, monkeypatch):
    voice_box["tts"].fail = True
    events = voice_box["events"]
    _install_delivery(monkeypatch, events)

    response = client.post("/webhooks/whatsapp", json=_payload(store, SURESH))

    body = response.json()
    assert body["reply"] == REPLY
    assert body["audio_produced"] is False
    assert _kinds(events) == ["download", "text"]
    assert events[1][2] == REPLY
    assert voice_box["store"].saved == []


def test_upload_failure_sends_text_only(client, store, graph, voice_box, dev_env, monkeypatch):
    events = voice_box["events"]
    _install_delivery(monkeypatch, events, upload_result={"status": "failed", "error": "meta down"})

    response = client.post("/webhooks/whatsapp", json=_payload(store, SURESH))

    assert response.json()["reply"] == REPLY
    assert response.json()["audio_produced"] is False
    assert _kinds(events) == ["download", "text", "convert", "upload"]


def test_conversion_failure_sends_text_only(client, store, graph, voice_box, dev_env, monkeypatch):
    events = voice_box["events"]
    _install_delivery(monkeypatch, events, convert=False)

    response = client.post("/webhooks/whatsapp", json=_payload(store, SURESH))

    assert response.json()["audio_produced"] is False
    assert response.json()["reply"] == REPLY
    assert _kinds(events) == ["download", "text", "convert"]


def test_audio_send_failure_keeps_the_text(client, store, graph, voice_box, dev_env, monkeypatch):
    events = voice_box["events"]
    _install_delivery(monkeypatch, events, audio_result={"status": "failed", "error": "rejected"})

    response = client.post("/webhooks/whatsapp", json=_payload(store, SURESH))

    assert response.json()["audio_produced"] is False
    assert response.json()["reply"] == REPLY
    assert _kinds(events) == ["download", "text", "convert", "upload", "audio"]


@pytest.mark.parametrize("sender", ["919999999999", MEENA])
def test_unauthorized_voice_note_does_not_download(client, store, graph, voice_box, monkeypatch, sender):
    events = voice_box["events"]
    _install_delivery(monkeypatch, events)

    response = client.post("/webhooks/whatsapp", json=_payload(store, sender))

    assert response.json()["status"] == "ignored"
    assert events == []
    assert graph.calls == []


def test_missing_token_logs_text_and_audio_without_claiming_delivery(
    client, store, graph, voice_box, dev_env, monkeypatch, capsys
):
    monkeypatch.setattr(settings, "whatsapp_token", "")

    async def fake_download(media_id, *, max_bytes, client=None):
        return WhatsAppMedia(b"OggS-inbound", "audio/ogg")

    def fake_convert(mp3):
        return b"OggS" + b"OpusHead" + b"reply"

    monkeypatch.setattr(routes_webhooks, "download_whatsapp_media", fake_download)
    monkeypatch.setattr(routes_webhooks, "mp3_to_ogg_opus", fake_convert)

    response = client.post("/webhooks/whatsapp", json=_payload(store, SURESH))

    body = response.json()
    assert body["status"] == "ok"
    assert body["delivery"] == "stubbed"
    assert body["reply"] == REPLY
    assert body["audio_produced"] is True
    logged = capsys.readouterr().out
    assert f"[whatsapp-stub] to={SURESH}:" in logged
    assert "[whatsapp-stub] upload mime=audio/ogg; codecs=opus" in logged
    assert f"[whatsapp-stub] audio to={SURESH}" in logged
    assert "media_id" not in logged
    assert voice_box["store"].saved == []


def test_production_acknowledgement_has_no_reply_body(
    client, store, graph, voice_box, monkeypatch
):
    monkeypatch.setattr(settings, "env", "production")
    _install_delivery(monkeypatch, voice_box["events"])

    voice = client.post("/webhooks/whatsapp", json=_payload(store, SURESH))
    text = client.post(
        "/webhooks/whatsapp",
        json=_payload(store, SURESH, kind="text", text="Was my son present?"),
    )

    for response in (voice, text):
        assert set(response.json()) == {"status", "delivery"}
        assert response.json()["status"] == "ok"


def test_text_message_reports_no_audio(client, store, graph, dev_env, monkeypatch):
    sent = []

    async def fake_text(to, body):
        sent.append((to, body))
        return {"status": "stubbed"}

    async def fail_upload(*_args, **_kwargs):
        raise AssertionError("text reply must not upload audio")

    monkeypatch.setattr(routes_webhooks, "send_whatsapp_message", fake_text)
    monkeypatch.setattr(routes_webhooks, "upload_media", fail_upload)

    response = client.post(
        "/webhooks/whatsapp", json=_payload(store, SURESH, kind="text", text="Was my son present?")
    )

    assert response.json()["audio_produced"] is False
    assert response.json()["reply"] == REPLY
    assert sent == [(SURESH, REPLY)]


def test_upload_and_audio_send_stub_without_a_token(monkeypatch, capsys):
    monkeypatch.setattr(settings, "whatsapp_token", "")

    def fail_client(*_args, **_kwargs):
        raise AssertionError("stub mode must not call the WhatsApp API")

    monkeypatch.setattr("app.services.whatsapp_service.httpx.AsyncClient", fail_client)
    uploaded = asyncio.run(upload_media(b"OggSvoice", "audio/ogg; codecs=opus"))
    assert uploaded == {"status": "stubbed"}
    assert "media_id" not in uploaded
    sent = asyncio.run(send_whatsapp_audio(SURESH, None))
    assert sent == {"status": "stubbed"}
    logged = capsys.readouterr().out
    assert "upload mime=audio/ogg; codecs=opus" in logged
    assert f"[whatsapp-stub] audio to={SURESH}" in logged
    assert "media_id" not in logged
    assert "stub-media" not in logged


def test_upload_and_audio_send_return_failed_status(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "555")

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("app.services.whatsapp_service.httpx.AsyncClient", BrokenClient)
    uploaded = asyncio.run(upload_media(b"OggSvoice", "audio/ogg"))
    sent = asyncio.run(send_whatsapp_audio(SURESH, "media-9"))
    assert uploaded["status"] == "failed"
    assert sent["status"] == "failed"
    assert "error" in uploaded and "error" in sent


def test_audio_send_marks_the_file_as_a_voice_note(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "555")
    posts = []

    class RecordingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            posts.append((url, kwargs))

            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    if url.endswith("/media"):
                        return {"id": "media-7"}
                    return {"messages": [{"id": "wamid.1"}]}

            return Response()

    monkeypatch.setattr("app.services.whatsapp_service.httpx.AsyncClient", RecordingClient)
    uploaded = asyncio.run(upload_media(b"OggSvoice", "audio/ogg; codecs=opus"))
    sent = asyncio.run(send_whatsapp_audio(SURESH, uploaded["media_id"]))
    assert uploaded == {"status": "uploaded", "media_id": "media-7"}
    assert sent["messages"][0]["id"] == "wamid.1"
    upload_url, upload_kwargs = posts[0]
    send_url, send_kwargs = posts[1]
    assert upload_url.endswith("/555/media")
    assert upload_kwargs["files"]["file"][2] == "audio/ogg; codecs=opus"
    assert send_url.endswith("/555/messages")
    assert send_kwargs["json"]["type"] == "audio"
    assert send_kwargs["json"]["audio"] == {"id": "media-7", "voice": True}


def test_download_without_token_explains_why(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "")
    with pytest.raises(WhatsAppMediaError) as exc:
        asyncio.run(download_whatsapp_media("media-1", max_bytes=100))
    assert exc.value.reason == "unavailable"
    assert "WHATSAPP_TOKEN" in exc.value.detail


def test_download_does_not_follow_an_untrusted_url(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "token")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={"url": "https://evil.example/steal", "mime_type": "audio/ogg", "file_size": 4},
        )

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            with pytest.raises(WhatsAppMediaError) as exc:
                await download_whatsapp_media("media-1", max_bytes=1000, client=client)
            assert exc.value.reason == "unavailable"

    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0].endswith("/media-1")
