"""LangGraph orchestrator: classifies intent, routes to one specialist
agent node, then ends the turn. Checkpointed per-conversation so the
communication agent keeps context across turns without every agent
needing to re-fetch history.

Intent comes from the trained embedding classifier when it is confident,
otherwise from the LLM router. The insight agent is not routed here - it
runs as a batch job (app/tasks/daily_batch.py)."""

import logging

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.agents.state import AgentState
from app.agents.daily_update_agent import daily_update_node
from app.agents.academic_agent import academic_node
from app.agents.communication_agent import communication_node
from app.agents.parent_teacher_agent import parent_teacher_node
from app.agents.intent_classifier import IntentClassifier

logger = logging.getLogger(__name__)

router_llm = ChatOpenAI(model=settings.openai_model_fast, api_key=settings.openai_api_key)
intent_classifier = IntentClassifier.load()

# Route definitions double as the labeling guide for data/intent/*.jsonl.
ROUTER_PROMPT = """Classify the parent's latest message into exactly one route.
Parents write in English, Hindi, Hinglish (romanized Hindi), or Telugu.

- "daily_update" - a specific day at school: attendance, being late or
  leaving early, homework, diary notes, care log (nap, meals, mood),
  today's class activity.
  e.g. "Was my child present today?", "Aaj ka homework kya hai?",
  "ఈరోజు హోంవర్క్ ఏమిటి?"
- "academic" - results and progress: marks, test/FA/SA scores, rank card,
  report card, syllabus progress or chapters completed, developmental
  milestones, whether performance is improving or dropping.
  e.g. "How did she do in FA1 Maths?", "Mere bete ka rank kya hai?",
  "हिंदी का सिलेबस कितना पूरा हुआ?"
- "parent_teacher" - reaching or asking about a teacher: message or inform
  a teacher (late, leave, absence notice), meeting or call requests, who
  teaches a subject, teacher contact details, special requests for the
  teacher, complaints about a teacher, and behaviour, bullying, wellbeing,
  or safety concerns that need the teacher's attention.
  e.g. "Please tell the class teacher she'll be late", "Who teaches Maths?",
  "Some kids keep pushing my son", "Class teacher se baat karni hai"
- "communication" - anything else: school timings, holidays, events, fees,
  transport, uniform, books, exam timetable, what is taught in the
  curriculum, app settings, greetings and thanks.
  e.g. "When is the annual day?", "Fees kaise bharni hai?", "Thank you!"

Reply with only the route name, nothing else."""

ROUTE_TO_NODE = {
    "daily_update": "daily_update",
    "academic": "academic",
    "parent_teacher": "parent_teacher",
    "communication": "communication",
}


def _latest_user_text(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            return msg.content.strip()
    return ""


def _llm_route(state: AgentState) -> str:
    messages = [SystemMessage(content=ROUTER_PROMPT)] + state["messages"]
    response = router_llm.invoke(messages)
    route = response.content.strip().strip("\"'").lower()
    if route not in ROUTE_TO_NODE:
        route = "communication"  # safe default - most flexible agent
    return route


def router_node(state: AgentState) -> dict:
    text = _latest_user_text(state["messages"])
    if intent_classifier is not None and text:
        try:
            prediction = intent_classifier.predict(text)
        except Exception:  # noqa: BLE001 - any classifier failure falls back to the LLM
            logger.warning("intent classifier failed; using LLM router", exc_info=True)
        else:
            if (
                prediction.intent in ROUTE_TO_NODE
                and prediction.confidence >= settings.intent_confidence_threshold
            ):
                return {
                    "route_to": prediction.intent,
                    "intent": prediction.intent,
                    "intent_source": "classifier",
                    "intent_confidence": prediction.confidence,
                }

    route = _llm_route(state)
    return {"route_to": route, "intent": route, "intent_source": "llm", "intent_confidence": None}


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
