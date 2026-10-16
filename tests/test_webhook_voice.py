"""WhatsApp voice notes: authorized guardians only, validated before
transcription, answered as text on the same conversation thread as text."""

import io
import shutil
import wave
from pathlib import Path

import pytest

from app.api import routes_webhooks
from app.config import settings
from app.services import chat_service
from app.services.whatsapp_service import WhatsAppMedia, WhatsAppMediaError
from app.voice.audio import AudioToolError, InvalidAudioError
from app.voice.service import VoiceService
from app.voice.storage import AudioStore
from app.voice.stt import Transcript
from tests.fakes import FakeGraph

SURESH = "919000000005"   # father of Rohan, consent given (guardian g-5-1)
MEENA = "919000000006"    # mother of Vikram, no consent
VOICE_NOTE = (Path(__file__).parent / "fixtures" / "voice_note.ogg").read_bytes()
REPLY = "Rohan was absent on 6 October."
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")


class RecordingStt:
    def __init__(self):
        self.calls = []
        self.result = Transcript("Was my son present today?", "en", 1.0, None)
        self.error = None

    def load(self):
        return None

    def transcribe(self, audio, content_type, language=None, initial_prompt=None):
        self.calls.append((audio, content_type))
        if self.error:
            raise self.error
        return self.result


class NoTts:
    async def synthesize(self, text, language):
        raise AssertionError("WhatsApp voice notes are answered as text")


class Runtime:
    def __init__(self, service, state="ready"):
        self.service = service
        self.state = state


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph(reply=REPLY)
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


@pytest.fixture
def stt(client):
    from app.main import app

    old = getattr(app.state, "voice_runtime", None)
    recorder = RecordingStt()
    app.state.voice_runtime = Runtime(VoiceService(recorder, NoTts(), AudioStore()))
    yield recorder
    app.state.voice_runtime = old


class Downloads:
    def __init__(self):
        self.calls = []
        self.media = WhatsAppMedia(VOICE_NOTE, "audio/ogg")
        self.error = None

    async def __call__(self, media_id, *, max_bytes):
        self.calls.append((media_id, max_bytes))
        if self.error:
            raise self.error
        return self.media


@pytest.fixture
def downloads(monkeypatch):
    fake = Downloads()
    monkeypatch.setattr(routes_webhooks, "download_whatsapp_media", fake)
    return fake


@pytest.fixture
def audio_tools(monkeypatch):
    """Deterministic stand-ins for ffmpeg; tests change .duration / .error."""

    class Tools:
        duration = 1.0
        error = None
        converted = []

    def duration(data, *, limit=None):
        if Tools.error:
            raise Tools.error
        return Tools.duration

    def convert(data, *, max_seconds=None):
        Tools.converted.append(data)
        return b"RIFF-converted-wav"

    Tools.converted = []
    monkeypatch.setattr(routes_webhooks, "audio_duration", duration)
    monkeypatch.setattr(routes_webhooks, "to_whisper_wav", convert)
    return Tools


def audio_payload(from_number, store, audio=None, phone_number_id=None):
    if phone_number_id is None:
        phone_number_id = store.rows("tenants")[0]["whatsapp_phone_number_id"]
    if audio is None:
        audio = {"id": "111", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_number_id},
        "messages": [{"from": from_number, "type": "audio", "audio": audio}],
    }}]}]}


def text_payload(from_number, store, text="Was my son present?"):
    phone_number_id = store.rows("tenants")[0]["whatsapp_phone_number_id"]
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_number_id},
        "messages": [{"from": from_number, "type": "text", "text": {"body": text}}],
    }}]}]}


def assert_nothing_happened(downloads, stt, graph, sent):
    assert downloads.calls == []
    assert stt.calls == []
    assert graph.calls == []
    assert sent == []


@needs_ffmpeg
def test_voice_note_is_converted_transcribed_and_answered(client, store, graph, sent, stt, downloads):
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))

    assert res.json()["status"] == "ok"
    assert downloads.calls == [("111", settings.voice_max_bytes)]
    wav, content_type = stt.calls[0]
    assert content_type == "audio/wav"
    with wave.open(io.BytesIO(wav), "rb") as source:
        assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (16_000, 1, 2)
    assert graph.calls[0]["state"]["messages"][0].content == "Was my son present today?"
    assert graph.calls[0]["state"]["guardian_id"] == "g-5-1"
    assert graph.calls[0]["config"] == {"configurable": {"thread_id": "g-5-1:whatsapp"}}
    assert sent == [(SURESH, REPLY)]
    conversations = store.find("conversations", guardian_id="g-5-1")
    assert [c["channel"] for c in conversations] == ["whatsapp"]


def test_voice_and_text_share_one_conversation(client, store, graph, sent, stt, downloads, audio_tools):
    client.post("/webhooks/whatsapp", json=text_payload(SURESH, store))
    client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))

    assert len(graph.calls) == 2
    assert graph.calls[0]["config"] == graph.calls[1]["config"] == {
        "configurable": {"thread_id": routes_webhooks.whatsapp_thread_id({"id": "g-5-1"})}
    }
    assert len(store.find("conversations", guardian_id="g-5-1")) == 1
    assert sent == [(SURESH, REPLY), (SURESH, REPLY)]


def test_unknown_guardian_triggers_nothing(client, store, graph, sent, stt, downloads):
    res = client.post("/webhooks/whatsapp", json=audio_payload("919999999999", store))
    assert res.json() == {"status": "ignored", "reason": "sender is not a registered guardian"}
    assert_nothing_happened(downloads, stt, graph, sent)


def test_guardian_without_consent_triggers_nothing(client, store, graph, sent, stt, downloads):
    res = client.post("/webhooks/whatsapp", json=audio_payload(MEENA, store))
    assert res.json() == {"status": "ignored", "reason": "no consent"}
    assert_nothing_happened(downloads, stt, graph, sent)


def test_unknown_business_number_triggers_nothing(client, store, graph, sent, stt, downloads):
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store, phone_number_id="123"))
    assert res.json()["reason"] == "unknown business number"
    assert_nothing_happened(downloads, stt, graph, sent)


@pytest.mark.parametrize("audio", [{}, {"id": ""}, {"id": None}, {"mime_type": "audio/ogg"}, "111", None])
def test_missing_media_id_or_malformed_audio_is_ignored(client, store, graph, sent, stt, downloads, audio):
    payload = audio_payload(SURESH, store)
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["audio"] = audio
    res = client.post("/webhooks/whatsapp", json=payload)
    assert res.json() == {"status": "ignored", "reason": "unsupported payload"}
    assert_nothing_happened(downloads, stt, graph, sent)


def test_unsupported_message_type_is_ignored(client, store, graph, sent, stt, downloads):
    payload = audio_payload(SURESH, store)
    payload["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": SURESH, "type": "image", "image": {"id": "222", "mime_type": "image/jpeg"},
    }
    res = client.post("/webhooks/whatsapp", json=payload)
    assert res.json()["status"] == "ignored"
    assert_nothing_happened(downloads, stt, graph, sent)


def test_too_long_voice_note_is_rejected_before_transcription(
    client, store, graph, sent, stt, downloads, audio_tools, monkeypatch
):
    monkeypatch.setattr(settings, "voice_max_seconds", 45)
    audio_tools.duration = 45.5
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "too long"
    assert sent == [(SURESH, "Your voice note is too long. Please send one that is 45 seconds or shorter.")]
    assert stt.calls == [] and graph.calls == [] and audio_tools.converted == []


def test_voice_limits_default_and_must_be_positive(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    for name in ("VOICE_MAX_SECONDS", "VOICE_MAX_BYTES"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(openai_api_key="x", _env_file=None)
    assert (defaults.voice_max_seconds, defaults.voice_max_bytes) == (60, 5_242_880)
    for bad in ({"voice_max_seconds": 0}, {"voice_max_bytes": -1}):
        with pytest.raises(ValidationError):
            Settings(openai_api_key="x", _env_file=None, **bad)


def test_duration_reply_uses_the_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "voice_max_seconds", 60)
    assert "60 seconds" in routes_webhooks.too_long_reply()


@pytest.mark.parametrize("too_large", ["declared", "downloaded"])
def test_too_large_voice_note_is_rejected_before_transcription(
    client, store, graph, sent, stt, downloads, audio_tools, monkeypatch, too_large
):
    if too_large == "declared":
        downloads.error = WhatsAppMediaError("too_large", "media download exceeded the limit")
    else:
        monkeypatch.setattr(settings, "voice_max_bytes", len(VOICE_NOTE) - 1)
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "too large"
    assert sent == [(SURESH, routes_webhooks.TOO_LARGE_REPLY)]
    assert stt.calls == [] and graph.calls == []


def test_non_audio_declared_type_is_rejected_without_download(client, store, graph, sent, stt, downloads):
    payload = audio_payload(SURESH, store, audio={"id": "111", "mime_type": "application/pdf"})
    res = client.post("/webhooks/whatsapp", json=payload)
    assert res.json()["reason"] == "unsupported media type"
    assert sent == [(SURESH, routes_webhooks.UNSUPPORTED_REPLY)]
    assert downloads.calls == [] and stt.calls == [] and graph.calls == []


def test_non_audio_media_type_from_metadata_is_rejected(client, store, graph, sent, stt, downloads):
    downloads.media = WhatsAppMedia(b"%PDF-1.4", "application/pdf")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "unsupported media type"
    assert stt.calls == [] and graph.calls == []


def test_empty_download_is_rejected(client, store, graph, sent, stt, downloads):
    downloads.media = WhatsAppMedia(b"", "audio/ogg")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "empty audio"
    assert sent == [(SURESH, routes_webhooks.UNSUPPORTED_REPLY)]
    assert stt.calls == []


def test_undecodable_audio_is_rejected(client, store, graph, sent, stt, downloads, audio_tools):
    audio_tools.error = InvalidAudioError("ffmpeg could not decode the audio")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "undecodable audio"
    assert sent == [(SURESH, routes_webhooks.UNSUPPORTED_REPLY)]
    assert stt.calls == [] and graph.calls == []


@needs_ffmpeg
def test_corrupt_ogg_is_rejected_by_real_ffmpeg(client, store, graph, sent, stt, downloads):
    downloads.media = WhatsAppMedia(b"OggS" + b"\0" * 200, "audio/ogg")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "undecodable audio"
    assert stt.calls == [] and graph.calls == []


def test_missing_ffmpeg_gives_unavailable_reply(client, store, graph, sent, stt, downloads, audio_tools):
    audio_tools.error = AudioToolError("ffmpeg is not installed or not on PATH")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "conversion unavailable"
    assert sent == [(SURESH, routes_webhooks.UNAVAILABLE_REPLY)]
    assert stt.calls == []


def test_missing_whatsapp_token_gives_unavailable_reply(client, store, graph, sent, stt, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "download failed"
    assert sent == [(SURESH, routes_webhooks.UNAVAILABLE_REPLY)]
    assert stt.calls == [] and graph.calls == []


def test_download_failure_gives_unavailable_reply(client, store, graph, sent, stt, downloads):
    downloads.error = WhatsAppMediaError("unavailable", "media download returned HTTP 500")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "download failed"
    assert sent == [(SURESH, routes_webhooks.UNAVAILABLE_REPLY)]


def test_voice_disabled_gives_unavailable_reply_without_download(client, store, graph, sent, downloads):
    from app.main import app

    old = getattr(app.state, "voice_runtime", None)
    app.state.voice_runtime = Runtime(None, state="disabled")
    try:
        res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    finally:
        app.state.voice_runtime = old
    assert res.json()["reason"] == "voice unavailable"
    assert sent == [(SURESH, routes_webhooks.UNAVAILABLE_REPLY)]
    assert downloads.calls == []


def test_transcription_failure_gives_unavailable_reply(client, store, graph, sent, stt, downloads, audio_tools):
    stt.error = RuntimeError("model crashed with secret details")
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["reason"] == "processing failed"
    assert sent == [(SURESH, routes_webhooks.UNAVAILABLE_REPLY)]
    assert "secret" not in sent[0][1]
    assert graph.calls == []


def test_empty_transcript_asks_to_repeat_without_agent(client, store, graph, sent, stt, downloads, audio_tools):
    stt.result = Transcript("", None, 0.0, None)
    res = client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert res.json()["status"] == "ok"
    assert sent == [(SURESH, "I could not hear a clear question. Please try again.")]
    assert graph.calls == []


def test_hindi_voice_note_gets_hindi_reply_language(client, store, graph, sent, stt, downloads, audio_tools):
    stt.result = Transcript("क्या मेरा बेटा आज स्कूल आया?", "hi", 1.0, None)
    client.post("/webhooks/whatsapp", json=audio_payload(SURESH, store))
    assert graph.calls[0]["state"]["language"] == "hi"
