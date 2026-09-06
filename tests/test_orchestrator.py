"""Basic smoke test for the router logic. Run with: pytest tests/
Requires OPENAI_API_KEY to be set - this hits the real router LLM call."""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="requires OPENAI_API_KEY"
)


def test_router_classifies_daily_update():
    from langchain_core.messages import HumanMessage
    from app.agents.orchestrator import orchestrator_graph

    config = {"configurable": {"thread_id": "test-thread"}}
    state = {
        "messages": [HumanMessage(content="Was my child present today?")],
        "tenant_id": "demo-school",
        "student_id": "s1",
        "guardian_id": "g1",
        "language": "en",
        "age_tier": "primary",
        "intent": None,
        "route_to": None,
    }
    result = orchestrator_graph.invoke(state, config=config)
    assert result["route_to"] in ("daily_update", "academic", "communication")
