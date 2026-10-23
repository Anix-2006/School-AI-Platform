import asyncio
import threading
import wave
from pathlib import Path

import pytest

from app.config import settings
from app.voice.service import VoiceService
from app.voice.stt import Transcript
from app.voice.storage import AudioStore
from app.voice.vad import Pcm16Resampler, segmenter_from_settings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "three_questions.wav"


class FakeStt:
    def __init__(self):
        self.calls = 0
        self.audio = []

    def load(self):
        return None

    def transcribe(self, audio_wav, language=None, initial_prompt=None):
        self.calls += 1
        self.audio.append(audio_wav)
        return Transcript(f"Question {self.calls}", language, duration_seconds=0.4, confidence=1.0)


class FakeTts:
    SUPPORTED_LANGUAGES = {"en", "hi", "te"}

    def __init__(self):
        self.calls = []

    def speak(self, text, language):
        self.calls.append((text, language))
        return b"ID3speech"


class FakeRuntime:
    state = "ready"

    def __init__(self, service):
        self.service = service

    def status(self):
        return {"status": self.state}


class HoldingChat:
    """Block the first turn so a later utterance can cancel it."""

    def __init__(self, service):
        self.service = service
        self.audio_store = service.audio_store
        self.calls = 0
        self.release = asyncio.Event()

    async def chat(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            await self.release.wait()
        return await self.service.chat(**kwargs)


def _pcm() -> bytes:
    with wave.open(str(FIXTURE), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 16000
        return handle.readframes(handle.getnframes())


def _token(client) -> str:
    return client.headers["Authorization"].split(" ", 1)[1]


def _take(ws, predicate, limit=20):
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if predicate(message):
            return message, seen
    raise AssertionError(f"expected message not in {seen}")


@pytest.fixture
def live_voice(client, monkeypatch):
    from app.main import app
    from app.services import chat_service
    from tests.fakes import FakeGraph

    graph = FakeGraph()
    monkeypatch.setattr(chat_service, "orchestrator_graph", graph)
    stt = FakeStt()
    tts = FakeTts()
    previous = getattr(app.state, "voice_runtime", None)
    app.state.voice_runtime = FakeRuntime(VoiceService(stt, tts, AudioStore()))
    try:
        yield stt, tts, graph
    finally:
        app.state.voice_runtime = previous


def test_vad_splits_three_recorded_questions():
    events = segmenter_from_settings().push(_pcm())
    assert len(events.utterances) == 3
    assert all(len(item) > 16000 for item in events.utterances)


def test_vad_silence_setting_keeps_short_pauses_together(monkeypatch):
    monkeypatch.setattr(settings, "voice_vad_silence_ms", 5000)
    events = segmenter_from_settings().push(_pcm())
    assert len(events.utterances) < 3


def test_resampler_keeps_duration():
    pcm = _pcm()[: 16000 * 2]
    same = Pcm16Resampler(16000, 16000).push(pcm)
    assert same == pcm
    doubled = Pcm16Resampler(8000, 16000).push(pcm[:8000])
    assert abs(len(doubled) - len(pcm[:8000]) * 2) <= 4


def test_live_refuses_a_parent_without_consent(client, live_voice):
    stt, tts, graph = live_voice
    with client.websocket_connect(f"/voice/live?access_token={_token(client)}") as ws:
        ws.send_json({"type": "start", "guardian_id": "g-7-1", "session_id": "live"})
        message = ws.receive_json()
    assert message["type"] == "error"
    assert message["status"] == 403
    assert "consent" in message["detail"].lower()
    assert stt.calls == 0
    assert tts.calls == []
    assert graph.calls == []


def test_live_does_not_run_when_voice_is_disabled(client):
    with client.websocket_connect(f"/voice/live?access_token={_token(client)}") as ws:
        message = ws.receive_json()
    assert message == {"type": "error", "status": 404, "detail": "Not found"}


def test_live_answers_each_question_and_keeps_the_session(client, live_voice):
    stt, _tts, graph = live_voice
    with client.websocket_connect(f"/voice/live?access_token={_token(client)}") as ws:
        ws.send_json(
            {
                "type": "start",
                "guardian_id": "g-3-1",
                "session_id": "demo-live",
                "sample_rate": 16000,
            }
        )
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["silence_ms"] == settings.voice_vad_silence_ms
        assert ready["max_utterance_seconds"] == settings.voice_max_seconds
        assert ready["sensitivity"] == settings.voice_vad_sensitivity
        assert ws.receive_json()["state"] == "listening"
        ws.send_bytes(_pcm())
        replies = []
        for _ in range(3):
            reply, _seen = _take(ws, lambda item: item.get("type") == "reply")
            replies.append(reply)
            _take(ws, lambda item: item.get("state") == "speaking")
            ws.send_json({"type": "playback_done"})
    assert [item["transcript"] for item in replies] == ["Question 1", "Question 2", "Question 3"]
    assert all(item["language"] == "hi" for item in replies)
    assert all(item["reply"] == "Here is your update." for item in replies)
    assert all(item["agent_used"] == "daily_update" for item in replies)
    assert stt.calls == 3
    assert len({call["config"]["configurable"]["thread_id"] for call in graph.calls}) == 1
    assert graph.calls[0]["config"]["configurable"]["thread_id"] == "g-3-1:demo-live"
    assert all(call["state"]["channel"] == "voice" for call in graph.calls)


def test_live_barge_in_stops_the_current_reply(client, live_voice):
    stt, _tts, _graph = live_voice
    utterances = segmenter_from_settings().push(_pcm()).utterances
    with client.websocket_connect(f"/voice/live?access_token={_token(client)}") as ws:
        ws.send_json(
            {"type": "start", "guardian_id": "g-3-1", "session_id": "barge", "sample_rate": 16000}
        )
        _take(ws, lambda item: item.get("state") == "listening")
        ws.send_bytes(utterances[0])
        first, _seen = _take(ws, lambda item: item.get("type") == "reply")
        _take(ws, lambda item: item.get("state") == "speaking")
        ws.send_bytes(utterances[1])
        barge, seen = _take(ws, lambda item: item.get("type") == "barge_in")
        second, after = _take(ws, lambda item: item.get("type") == "reply")
    assert first["transcript"] == "Question 1"
    assert barge["type"] == "barge_in"
    assert all(item.get("type") != "reply" for item in seen)
    assert second["transcript"] == "Question 2"
    assert stt.calls == 2
    assert any(item.get("state") == "thinking" for item in after)


def test_live_cancels_a_reply_still_being_prepared(client, live_voice, monkeypatch):
    from app.main import app

    service = app.state.voice_runtime.service
    holding = HoldingChat(service)
    monkeypatch.setattr(app.state.voice_runtime, "service", holding)
    utterances = segmenter_from_settings().push(_pcm()).utterances
    with client.websocket_connect(f"/voice/live?access_token={_token(client)}") as ws:
        ws.send_json(
            {
                "type": "start",
                "guardian_id": "g-3-1",
                "session_id": "cancel",
                "sample_rate": 16000,
            }
        )
        _take(ws, lambda item: item.get("state") == "listening")
        ws.send_bytes(utterances[0])
        _take(ws, lambda item: item.get("state") == "thinking")
        ws.send_bytes(utterances[1])
        _take(ws, lambda item: item.get("type") == "barge_in")
        reply, _seen = _take(ws, lambda item: item.get("type") == "reply")
    assert reply["transcript"] == "Question 1"
    assert holding.calls == 2
    assert not holding.release.is_set()


class StreamingTts:
    """Emit a later sentence first internally, then deliver in index order."""

    SUPPORTED_LANGUAGES = {"en", "hi", "te"}

    def speak(self, text, language):
        return b"ID3speech"

    def speak_streaming(self, text, language, on_sentence, join=True):
        del text, language, join
        ready = [
            (1, "Bring the homework sheet.", b"ID3-1"),
            (0, "School is closed tomorrow.", b"ID3-0"),
        ]
        for index, sentence, audio in sorted(ready):
            on_sentence(index, sentence, audio)
        return b""


class GatedStreamingTts:
    """Hold the second sentence so the first can be delivered on its own."""

    def __init__(self):
        self.release = threading.Event()

    def speak(self, text, language):
        return b"ID3speech"

    def speak_streaming(self, text, language, on_sentence, join=True):
        del text, language, join
        on_sentence(0, "School is closed tomorrow.", b"ID3-0")
        if not self.release.wait(timeout=5):
            raise TimeoutError("second sentence was not released")
        on_sentence(1, "Bring the homework sheet.", b"ID3-1")
        return b""


def test_live_plays_the_first_sentence_before_the_rest_are_ready(client, live_voice):
    from app.main import app

    gated = GatedStreamingTts()
    app.state.voice_runtime.service.tts = gated
    utterances = segmenter_from_settings().push(_pcm()).utterances
    with client.websocket_connect(f"/voice/live?access_token={_token(client)}") as ws:
        ws.send_json(
            {
                "type": "start",
                "guardian_id": "g-3-1",
                "session_id": "early-play",
                "sample_rate": 16000,
            }
        )
        _take(ws, lambda item: item.get("state") == "listening")
        ws.send_bytes(utterances[0])
        first, seen = _take(ws, lambda item: item.get("type") == "sentence" and item.get("index") == 0)
        assert first["text"] == "School is closed tomorrow."
        assert first["audio_base64"]
        assert not any(item.get("type") == "reply" for item in seen)
        assert not gated.release.is_set()
        gated.release.set()
        reply, later = _take(ws, lambda item: item.get("type") == "reply")
    second = next(item for item in later if item.get("type") == "sentence" and item.get("index") == 1)
    assert second["text"] == "Bring the homework sheet."
    assert reply["audio_base64"] is None


def test_live_sends_sentences_in_order_before_the_reply(client, live_voice):
    from app.main import app

    app.state.voice_runtime.service.tts = StreamingTts()
    utterances = segmenter_from_settings().push(_pcm()).utterances
    with client.websocket_connect(f"/voice/live?access_token={_token(client)}") as ws:
        ws.send_json(
            {
                "type": "start",
                "guardian_id": "g-3-1",
                "session_id": "sentences",
                "sample_rate": 16000,
            }
        )
        _take(ws, lambda item: item.get("state") == "listening")
        ws.send_bytes(utterances[0])
        reply, seen = _take(ws, lambda item: item.get("type") == "reply")
    sentences = [item for item in seen if item.get("type") == "sentence"]
    assert [item["index"] for item in sentences] == [0, 1]
    assert sentences[0]["text"].startswith("School is closed")
    assert sentences[0]["audio_base64"]
    assert seen.index(sentences[0]) < seen.index(sentences[1]) < seen.index(reply)
    assert reply["audio_base64"] is None
    assert reply["reply"] == "Here is your update."
    assert any(item.get("state") == "speaking" and seen.index(item) < seen.index(sentences[1]) for item in seen)


def test_voice_page_offers_live_conversation(client):
    page = client.get("/flows/voice")
    assert page.status_code == 200
    assert 'id="live"' in page.text
    assert "Start conversation" in page.text
    assert 'id="liveStatus"' in page.text
    assert 'data-sample-rate="16000"' in page.text
    assert "__VOICE_SAMPLE_RATE__" not in page.text
    assert "barge_in" in page.text
    assert 'data.type === "sentence"' in page.text
    assert "sentenceQueue" in page.text
    assert "pumpSentences" in page.text
    worklet = client.get("/voice-live-worklet.js")
    assert worklet.status_code == 200
    assert "pcm-sender" in worklet.text
