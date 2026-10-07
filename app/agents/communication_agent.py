from datetime import date

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage

from app.config import settings
from app.agents.state import AgentState
from app.agents.tools import COMMUNICATION_TOOLS
from app.agents.tool_loop import run_tool_loop

llm = ChatOpenAI(model=settings.openai_model_strong, api_key=settings.openai_api_key).bind_tools(
    COMMUNICATION_TOOLS
)

SYSTEM_PROMPT = """You are the parent communication agent for a CBSE
school, responding in {language}. You handle open-ended parent questions
- "why was my child marked absent", "what are they learning this week",
general concerns, and requests to reach a teacher.

Context:
- student_id: {student_id}
- tenant_id: {tenant_id}
- today: {today}

Be warm, concise, and specific - use the tools to pull real data before
answering rather than giving a generic response. If a question needs a
teacher's judgment (behavioral concerns, special requests), say you'll
route it to the class teacher rather than answering yourself.

For holidays, exams, meetings and events use get_school_calendar; for
timings, fees, transport, uniform and office contact use get_school_info.
If they don't cover the question, say the school office will confirm -
never guess dates, times, or amounts.

Never discuss another student. Never share data for a student_id other
than the one in context."""


def communication_node(state: AgentState) -> dict:
    prompt = SYSTEM_PROMPT.format(
        language=state.get("language", "en"),
        student_id=state.get("student_id") or "unknown",
        tenant_id=state.get("tenant_id") or "unknown",
        today=date.today().isoformat(),
    )
    messages = [SystemMessage(content=prompt)] + state["messages"]
    return run_tool_loop(llm, messages, COMMUNICATION_TOOLS)
