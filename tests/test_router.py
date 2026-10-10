"""Router intent parsing and route mapping. Run with: pytest tests/test_router.py
Uses a fake router LLM, so no OPENAI_API_KEY or network access is needed."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents import orchestrator


class FakeRouterLLM:
    def __init__(self, reply: str):
        self.reply = reply

    def invoke(self, messages):
        return AIMessage(content=self.reply)


def _route_for(monkeypatch, llm_reply: str) -> str:
    monkeypatch.setattr(orchestrator, "router_llm", FakeRouterLLM(llm_reply))
    state = {"messages": [HumanMessage(content="Who teaches Math?")]}
    return orchestrator.router_node(state)["route_to"]


@pytest.mark.parametrize(
    "llm_reply, expected",
    [
        ("parent_teacher", "parent_teacher"),
        ("Parent_Teacher\n", "parent_teacher"),
        ('"parent_teacher"', "parent_teacher"),
        ("daily_update", "daily_update"),
        ("academic", "academic"),
        ("communication", "communication"),
        ("something_else", "communication"),
    ],
)
def test_router_node_parses_route(monkeypatch, llm_reply, expected):
    assert _route_for(monkeypatch, llm_reply) == expected


def test_router_prompt_lists_parent_teacher():
    assert '"parent_teacher"' in orchestrator.ROUTER_PROMPT


def test_parent_teacher_runs_on_its_own_node():
    assert orchestrator.ROUTE_TO_NODE["parent_teacher"] == "parent_teacher"
