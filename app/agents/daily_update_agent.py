from datetime import date

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage

from app.config import settings
from app.agents.state import AgentState
from app.agents.tools import DAILY_UPDATE_TOOLS
from app.agents.tool_loop import run_tool_loop

# Fast/cheap model - this agent handles high-volume, low-complexity daily
# pushes (attendance + diary summaries). Don't burn the strong model here.
llm = ChatOpenAI(model=settings.openai_model_fast, api_key=settings.openai_api_key).bind_tools(
    DAILY_UPDATE_TOOLS
)

SYSTEM_PROMPT = """You are the daily update agent for a CBSE school.
Summarize a student's attendance and diary entry for their parent in
{language}. Keep it under 3 short sentences, warm but factual.

Context:
- student_id: {student_id}
- today: {today}

Use tools to fetch attendance/diary before answering. Pass student_id and
today's date (YYYY-MM-DD) to tools. If tools return no record, say so —
never invent data.

If age_tier is pre_primary, focus on care notes (nap, meals, mood,
activity theme) - never mention marks or homework.
If age_tier is primary_lower or primary, focus on homework and any
attendance note."""


def daily_update_node(state: AgentState) -> dict:
    prompt = SYSTEM_PROMPT.format(
        language=state.get("language", "en"),
        student_id=state.get("student_id") or "unknown",
        today=date.today().isoformat(),
    )
    messages = [SystemMessage(content=prompt)] + state["messages"]
    return run_tool_loop(llm, messages, DAILY_UPDATE_TOOLS)
