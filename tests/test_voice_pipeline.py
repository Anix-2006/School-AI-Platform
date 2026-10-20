"""Shared voice pipeline: audio in → agents → audio out (issue #37)."""

from __future__ import annotations

from app.voice.constants import COULD_NOT_HEAR
from app.voice.pipeline import VoiceReply, run_voice_turn
from app.voice.speech_text import to_speech_text
from app.voice.stt import Transcript


class FakeStt:
    def __init__(self, text="Any homework today?", language="en"):
        self.result = Transcript(
            text=text, language=language, duration_seconds=1.0, confidence=1.0
        )
        self.calls: list[tuple[bytes, str | None]] = []

    def load(self) -> None:
        return None

    def transcribe(self, audio_wav: bytes, language: str | None = None) -> Transcript:
        self.calls.append((audio_wav, language))
        return self.result


class FakeTts:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def speak(self, text: str, language: str) -> bytes:
        self.calls.append((text, language))
        return f"ID3:{language}:{text}".encode()


def _parent(store):
    guardian = store.get("guardians", "g-3-1")
    student = store.get("students", guardian["student_id"])
    tenant_id = student["tenant_id"]
    return tenant_id, guardian, student


def test_to_speech_text_strips_markdown_and_emojis():
    cleaned = to_speech_text("**FA1:** 42/50 ✅ see [marks](https://example.com)")
    assert "**" not in cleaned
    assert "✅" not in cleaned
    assert "https://" not in cleaned
    assert "42 out of 50" in cleaned
    assert "formative assessment 1" in cleaned


def test_run_voice_turn_full_flow_logs_conversation(monkeypatch, store):
    from app.services import chat_service
    from tests.fakes import FakeGraph

    graph = FakeGraph(reply="**Homework** is page 12 ✅", route="academic")
    monkeypatch.setattr(chat_service, "orchestrator_graph", graph)

    tenant_id, guardian, student = _parent(store)
    stt = FakeStt(text="What is the homework?")
    tts = FakeTts()

    result = run_voice_turn(
        tenant_id,
        guardian,
        student,
        b"fake-wav",
        "voice",
        guardian["id"],
        stt=stt,
        tts=tts,
    )

    assert isinstance(result, VoiceReply)
    assert result.transcript == "What is the homework?"
    assert result.reply_text == "**Homework** is page 12 ✅"
    assert "**" not in result.speech_text
    assert "✅" not in result.speech_text
    assert result.audio.startswith(b"ID3:")
    assert result.agent_used == "academic"
    assert result.language == (guardian.get("preferred_language") or "en")

    assert graph.calls[0]["state"]["channel"] == "voice"
    assert graph.calls[0]["state"]["messages"][0].content == "What is the homework?"
    assert graph.calls[0]["config"]["configurable"]["thread_id"] == guardian["id"]

    conversation = next(
        item
        for item in store.find("conversations", guardian_id=guardian["id"])
        if item["channel"] == "voice"
    )
    messages = store.find("messages", conversation_id=conversation["id"])
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "What is the homework?"),
        ("assistant", "**Homework** is page 12 ✅"),
    ]


def test_run_voice_turn_passes_preferred_language(monkeypatch, store):
    from app.services import chat_service
    from tests.fakes import FakeGraph

    monkeypatch.setattr(chat_service, "orchestrator_graph", FakeGraph())
    tenant_id, guardian, student = _parent(store)
    guardian = {**guardian, "preferred_language": "hi"}
    stt = FakeStt(text="होमवर्क क्या है?", language="hi")
    tts = FakeTts()

    result = run_voice_turn(
        tenant_id,
        guardian,
        student,
        b"audio",
        "voice",
        f"{guardian['id']}:demo",
        stt=stt,
        tts=tts,
    )

    assert stt.calls[0][1] == "hi"
    assert tts.calls[0][1] == "hi"
    assert result.language == "hi"


def test_empty_transcript_skips_agents(monkeypatch, store):
    from app.services import chat_service

    def boom(*_args, **_kwargs):
        raise AssertionError("orchestrator must not run for empty transcript")

    monkeypatch.setattr(chat_service, "run_parent_turn", boom)
    tenant_id, guardian, student = _parent(store)
    guardian = {**guardian, "preferred_language": "en"}
    stt = FakeStt(text="   ")
    tts = FakeTts()

    result = run_voice_turn(
        tenant_id,
        guardian,
        student,
        b"silence",
        "voice",
        guardian["id"],
        stt=stt,
        tts=tts,
    )

    assert result.transcript == ""
    assert result.reply_text == COULD_NOT_HEAR["en"]
    assert result.speech_text == COULD_NOT_HEAR["en"]
    assert result.agent_used is None
    assert result.audio
    assert store.find("conversations", guardian_id=guardian["id"]) == []


def test_empty_transcript_uses_hindi_phrase(monkeypatch, store):
    from app.services import chat_service

    def boom(*_args, **_kwargs):
        raise AssertionError("orchestrator must not run for empty transcript")

    monkeypatch.setattr(chat_service, "run_parent_turn", boom)
    tenant_id, guardian, student = _parent(store)
    guardian = {**guardian, "preferred_language": "hi"}
    result = run_voice_turn(
        tenant_id,
        guardian,
        student,
        b"silence",
        "voice",
        guardian["id"],
        stt=FakeStt(text=""),
        tts=FakeTts(),
    )
    assert result.reply_text == COULD_NOT_HEAR["hi"]
    assert result.language == "hi"
