"""Graph routing smoke test.

The router and the daily-update model are faked, matching the other agent
tests, so this does not call OpenAI. Classifier-versus-LLM decisions are
covered in test_intent_classifier.py.
"""

from langchain_core.messages import AIMessage, HumanMessage

from app.agents import daily_update_agent, orchestrator


class FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content=self.reply)


def test_router_classifies_daily_update(monkeypatch):
    router = FakeLLM("daily_update")
    agent = FakeLLM("Present today.")
    monkeypatch.setattr(orchestrator, "router_llm", router)
    monkeypatch.setattr(daily_update_agent, "llm", agent)

    result = orchestrator.orchestrator_graph.invoke(
        {
            "messages": [HumanMessage(content="Was my child present today?")],
            "tenant_id": "demo-school",
            "student_id": "s-3-1",
            "guardian_id": "g-3-1",
            "language": "en",
            "age_tier": "primary",
            "intent": None,
            "route_to": None,
        },
        config={"configurable": {"thread_id": "test-router-daily-update"}},
    )

    assert result["route_to"] == "daily_update"
    assert result["intent_source"] == "llm"
    assert result["intent_confidence"] is None
    assert result["messages"][-1].content == "Present today."
    sent = [msg.content for call in router.calls for msg in call if isinstance(msg, HumanMessage)]
    assert "Was my child present today?" in sent
    assert agent.calls
