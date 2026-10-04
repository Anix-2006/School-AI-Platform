"""LangGraph orchestrator: classifies intent, routes to one specialist
agent node, then ends the turn. Checkpointed per-conversation so the
communication agent keeps context across turns without every agent
needing to re-fetch history."""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage

from app.config import settings
from app.agents.state import AgentState
from app.agents.daily_update_agent import daily_update_node
from app.agents.academic_agent import academic_node
from app.agents.communication_agent import communication_node
from app.agents.parent_teacher_agent import parent_teacher_node

router_llm = ChatOpenAI(model=settings.openai_model_fast, api_key=settings.openai_api_key)

ROUTER_PROMPT = """Classify the parent's message into exactly one route:
- "daily_update" - asking about today's attendance, homework, or care log
- "academic" - asking about marks, rank card, syllabus progress, milestones
- "parent_teacher" - wants to reach, message, or find out about a teacher
  (e.g. "Can you tell the class teacher she'll be late tomorrow?",
  "Who teaches Math?", "I want to meet the class teacher",
  "Please pass a message to the English teacher")
- "communication" - anything else (general questions, concerns)

Reply with only the route name, nothing else."""

ROUTE_TO_NODE = {
    "daily_update": "daily_update",
    "academic": "academic",
    "parent_teacher": "parent_teacher",
    "communication": "communication",
}


def router_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=ROUTER_PROMPT)] + state["messages"]
    response = router_llm.invoke(messages)
    route = response.content.strip().strip("\"'").lower()
    if route not in ROUTE_TO_NODE:
        route = "communication"  # safe default - most flexible agent
    return {"route_to": route}


def route_decision(state: AgentState) -> str:
    return state["route_to"]


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("router", router_node)
    graph.add_node("daily_update", daily_update_node)
    graph.add_node("academic", academic_node)
    graph.add_node("communication", communication_node)
    graph.add_node("parent_teacher", parent_teacher_node)

    graph.set_entry_point("router")
    graph.add_conditional_edges("router", route_decision, ROUTE_TO_NODE)
    graph.add_edge("daily_update", END)
    graph.add_edge("academic", END)
    graph.add_edge("communication", END)
    graph.add_edge("parent_teacher", END)

    # MemorySaver for dev - swap for a Postgres checkpointer in production
    # so conversation state survives restarts and scales across replicas.
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


orchestrator_graph = build_graph()
