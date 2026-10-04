"""Parent-teacher agent node and graph wiring. Run with:
pytest tests/test_parent_teacher_agent.py
Uses fake LLMs, so no OPENAI_API_KEY or network access is needed."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents import orchestrator, parent_teacher_agent
from app.agents.tools import PARENT_TEACHER_TOOLS


class FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content=self.reply)


def test_node_builds_prompt_from_state(monkeypatch):
    fake = FakeLLM("I'll share this with the class teacher.")
    monkeypatch.setattr(parent_teacher_agent, "llm", fake)

    result = parent_teacher_agent.parent_teacher_node(
        {
            "messages": [HumanMessage(content="Please tell the class teacher she'll be late.")],
            "student_id": "s1",
            "language": "hi",
        }
    )

    system = fake.calls[0][0]
    assert isinstance(system, SystemMessage)
    assert "student_id: s1" in system.content
    assert "writing to a parent in hi" in system.content
    assert "never ask which child" in system.content
    assert "No student is selected" not in system.content
    assert result["messages"][-1].content == "I'll share this with the class teacher."


def test_node_marks_missing_student_as_unknown(monkeypatch):
    fake = FakeLLM("Which child is this about?")
    monkeypatch.setattr(parent_teacher_agent, "llm", fake)

    parent_teacher_agent.parent_teacher_node(
        {"messages": [HumanMessage(content="Who teaches Math?")], "student_id": None}
    )

    system = fake.calls[0][0].content
    assert "student_id: unknown" in system
    assert "Ask the parent which child" in system
    assert "never ask which child" not in system


def test_only_light_tools_are_bound():
    assert [t.name for t in PARENT_TEACHER_TOOLS] == [
        "get_student_profile",
        "get_teachers_for_student",
    ]


def test_prompt_rules():
    prompt = parent_teacher_agent.SYSTEM_PROMPT
    assert "Never invent teacher names" in prompt
    assert "Only name teachers returned by get_teachers_for_student" in prompt
    assert "Never share a teacher's phone number or email" in prompt
    assert "escalated to the class teacher" in prompt
    assert "Only discuss the student in context" in prompt


def test_graph_routes_parent_teacher_to_new_node(monkeypatch):
    monkeypatch.setattr(orchestrator, "router_llm", FakeLLM("parent_teacher"))
    monkeypatch.setattr(parent_teacher_agent, "llm", FakeLLM("Sent to the class teacher."))

    result = orchestrator.build_graph().invoke(
        {
            "messages": [HumanMessage(content="Can you tell the class teacher she'll be late?")],
            "tenant_id": "demo-school",
            "student_id": "s1",
            "guardian_id": "g1",
            "language": "en",
            "age_tier": None,
            "intent": None,
            "route_to": None,
        },
        config={"configurable": {"thread_id": "test-parent-teacher"}},
    )

    assert result["route_to"] == "parent_teacher"
    assert result["messages"][-1].content == "Sent to the class teacher."
