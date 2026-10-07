"""WhatsApp webhook: only registered guardians with consent get a reply."""

import pytest

from app.api import routes_webhooks
from app.services import chat_service
from tests.fakes import FakeGraph

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
