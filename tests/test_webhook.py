"""WhatsApp webhook: only registered guardians with consent get a reply."""

import io
import threading
import time
import wave

import pytest

from app.api import routes_webhooks
from app.services import chat_service
from app.voice.service import VoiceService
from app.voice.storage import AudioStore
from tests.fakes import FakeGraph
from tests.test_voice import FakeRuntime, FakeStt, FakeTts

SURESH = "919000000005"   # father of Rohan, consent given
MEENA = "919000000006"    # mother of Vikram, no consent


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph(reply="Rohan was absent on 6 October.")
    monkeypatch.setattr(chat_service, "orchestrator_graph", fake)
    return fake


@pytest.fixture
def sent(monkeypatch):
    outbox = []

    async def fake_send(to, body):
        outbox.append((to, body))
        return {"status": "stubbed"}

    monkeypatch.setattr(routes_webhooks, "send_whatsapp_message", fake_send)
    return outbox


def payload(from_number, text="Was my son present?", phone_number_id=None, store=None):
    if phone_number_id is None:
        phone_number_id = store.rows("tenants")[0]["whatsapp_phone_number_id"]
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_number_id},
        "messages": [{"from": from_number, "type": "text", "text": {"body": text}}],
    }}]}]}


def test_registered_guardian_gets_reply(client, graph, sent, store):
    res = client.post("/webhooks/whatsapp", json=payload(SURESH, store=store))
    assert res.json()["status"] == "ok"
    assert sent == [(SURESH, "Rohan was absent on 6 October.")]

    state = graph.calls[0]["state"]
    assert state["guardian_id"] == "g-5-1"
    assert state["student_id"] == "s-5-1"
    assert state["language"] == "en"

    conversation = store.find("conversations", guardian_id="g-5-1")[0]
    assert conversation["channel"] == "whatsapp"


def test_unknown_number_gets_no_reply(client, graph, sent, store):
    res = client.post("/webhooks/whatsapp", json=payload("919999999999", store=store))
    assert res.json() == {"status": "ignored", "reason": "sender is not a registered guardian"}
    assert graph.calls == [] and sent == []


def test_guardian_without_consent_gets_no_reply(client, graph, sent, store):
    res = client.post("/webhooks/whatsapp", json=payload(MEENA, store=store))
    assert res.json() == {"status": "ignored", "reason": "no consent"}
    assert graph.calls == [] and sent == []


def test_unknown_business_number_is_ignored(client, graph, sent, store):
    res = client.post("/webhooks/whatsapp", json=payload(SURESH, phone_number_id="123", store=store))
    assert res.json()["reason"] == "unknown business number"
    assert graph.calls == []


def test_malformed_payload_is_ignored(client, graph, sent):
    res = client.post("/webhooks/whatsapp", json={"entry": []})
    assert res.json()["status"] == "ignored"


def audio_payload(from_number, store, media_id="media-1", phone_number_id=None):
    if phone_number_id is None:
        phone_number_id = store.rows("tenants")[0]["whatsapp_phone_number_id"]
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_number_id},
        "messages": [{
            "from": from_number,
            "type": "audio",
            "audio": {"id": media_id, "mime_type": "audio/wav"},
        }],
    }}]}]}


def _wav_bytes(seconds=0.05):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * int(8000 * seconds))
    return target.getvalue()


def test_voice_note_acks_before_transcription_finishes(client, graph, sent, store, monkeypatch):
    from app.main import app

    started = threading.Event()
    stt = FakeStt(delay=1.5)
    original = stt.transcribe

    def transcribe(*args):
        started.set()
        return original(*args)

    stt.transcribe = transcribe
    app.state.voice_runtime = FakeRuntime(VoiceService(stt, FakeTts(), AudioStore()))

    async def fake_download(_media_id):
        return _wav_bytes(), "audio/wav"

    monkeypatch.setattr(routes_webhooks, "download_whatsapp_media", fake_download)

    began = time.perf_counter()
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    elapsed = time.perf_counter() - began
    assert res.json() == {"status": "accepted"}
    assert elapsed < 1.0
    assert started.wait(timeout=5)
    deadline = time.monotonic() + 8
    while stt.calls < 1 or not sent:
        if time.monotonic() > deadline:
            break
        time.sleep(0.05)
    assert sent == [(SURESH, "Rohan was absent on 6 October.")]
    assert stt.calls == 1
