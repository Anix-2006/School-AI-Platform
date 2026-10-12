"""POST /chat: the guardian row decides student, language and consent."""

import pytest

from app.services import chat_service
from tests.fakes import FakeGraph

PRIYA = "g-3-1"     # mother of Ananya (s-3-1), Hindi, consent given
MEENA = "g-7-1"     # mother of Vikram (s-7-1), no consent


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(chat_service, "orchestrator_graph", fake)
    return fake


def test_reply_uses_guardian_child_and_language(client, graph):
    res = client.post("/chat", json={"guardian_id": PRIYA, "message": "Any homework?"})
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "Here is your update."
    assert body["agent_used"] == "daily_update"
    assert body["student_id"] == "s-3-1"
    assert body["language"] == "hi"

    state = graph.calls[0]["state"]
    assert state["student_id"] == "s-3-1"
    assert state["language"] == "hi"
    assert state["age_tier"] == "primary"
    assert graph.calls[0]["config"]["configurable"]["thread_id"] == PRIYA


def test_matching_student_id_is_accepted(client, graph):
    res = client.post("/chat", json={"guardian_id": PRIYA, "student_id": "s-3-1", "message": "hi"})
    assert res.status_code == 200


def test_other_childs_student_id_is_rejected(client, graph):
    res = client.post("/chat", json={"guardian_id": PRIYA, "student_id": "s-5-1", "message": "hi"})
    assert res.status_code == 403
    assert "not linked" in res.json()["detail"]
    assert graph.calls == []


def test_unknown_guardian_is_404(client, graph):
    res = client.post("/chat", json={"guardian_id": "g-nobody", "message": "hi"})
    assert res.status_code == 404
    assert graph.calls == []


def test_guardian_without_consent_gets_no_reply(client, graph):
    res = client.post("/chat", json={"guardian_id": MEENA, "message": "hi"})
    assert res.status_code == 403
    assert "consent" in res.json()["detail"]
    assert graph.calls == []


def test_session_id_gives_a_separate_memory_thread(client, graph):
    client.post("/chat", json={"guardian_id": PRIYA, "message": "hi", "session_id": "abc"})
    assert graph.calls[0]["config"]["configurable"]["thread_id"] == f"{PRIYA}:abc"


def test_turn_is_saved_to_conversations_and_messages(client, graph, store):
    client.post("/chat", json={"guardian_id": PRIYA, "message": "First question"})
    client.post("/chat", json={"guardian_id": PRIYA, "message": "Second question"})

    conversations = store.find("conversations", guardian_id=PRIYA)
    assert len(conversations) == 1
    assert conversations[0]["channel"] == "app"

    messages = store.find("messages", conversation_id=conversations[0]["id"])
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "First question"
    assert messages[0]["student_id"] == "s-3-1"
    assert messages[1]["student_id"] == "s-3-1"
    assert messages[1]["agent"] == "daily_update"
    assert messages[0]["intent_source"] == "llm"
    assert messages[1]["intent_confidence"] is None


def test_turn_metadata_follows_route_and_intent(client, store, monkeypatch):
    from langchain_core.messages import AIMessage

    class Sequenced:
        def __init__(self):
            self.pending = [
                {"route_to": "parent_teacher", "intent_source": "classifier", "intent_confidence": 0.96},
                {"route_to": "communication", "intent_source": "llm", "intent_confidence": None},
            ]

        def invoke(self, state, config=None):
            spec = self.pending.pop(0)
            return {"messages": [*state["messages"], AIMessage(content="Saved reply.")], **spec}

    monkeypatch.setattr(chat_service, "orchestrator_graph", Sequenced())
    assert client.post("/chat", json={"guardian_id": PRIYA, "message": "Who teaches Maths?"}).status_code == 200
    assert client.post("/chat", json={"guardian_id": PRIYA, "message": "Thank you"}).status_code == 200

    messages = store.find("messages", conversation_id=store.find("conversations", guardian_id=PRIYA)[0]["id"])
    parent, assistant = messages[0], messages[1]
    assert parent["student_id"] == assistant["student_id"] == "s-3-1"
    assert parent["intent_source"] == assistant["intent_source"] == "classifier"
    assert parent["intent_confidence"] == assistant["intent_confidence"] == 0.96
    assert assistant["agent"] == "parent_teacher"
    fallback_user, fallback_assistant = messages[2], messages[3]
    assert fallback_user["intent_source"] == fallback_assistant["intent_source"] == "llm"
    assert fallback_user["intent_confidence"] is None
    assert fallback_assistant["agent"] == "communication"
    assert fallback_assistant["content"] == "Saved reply."


def test_reply_still_returned_when_workbook_is_locked(client, graph, monkeypatch):
    from app.services import excel_store

    def locked(*_args, **_kwargs):
        raise PermissionError("open in Excel")

    monkeypatch.setattr(excel_store.os, "replace", locked)
    res = client.post("/chat", json={"guardian_id": PRIYA, "message": "hi"})
    assert res.status_code == 200
    assert res.json()["reply"] == "Here is your update."
