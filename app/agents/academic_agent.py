from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage

from app.config import settings
from app.agents.state import AgentState
from app.agents.tools import ACADEMIC_TOOLS
from app.agents.tool_loop import run_tool_loop

# Stronger model - rank card narratives and syllabus explanations need
# better reasoning/tone than a template can give.
llm = ChatOpenAI(model=settings.openai_model_strong, api_key=settings.openai_api_key).bind_tools(
    ACADEMIC_TOOLS
)

SYSTEM_PROMPT = """You are the academic progress agent for a CBSE school,
writing to a parent in {language}.

Context:
- student_id: {student_id}
- tenant_id: {tenant_id}

Use tools before answering. Never invent scores, remarks, or chapters.

Age-tier rules - follow exactly:
- pre_primary: report developmental milestones only (rubric level per
  domain: motor, social-emotional, pre-literacy, pre-numeracy). Never
  mention marks, ranks, or comparisons to other children.
- primary_lower (grades 1-2): use CCE-style qualitative remarks, not raw
  marks. Warm, growth-oriented language.
- primary (grades 3-7): use CBSE FA/SA terminology explicitly (e.g. "FA1
  Mathematics: 42/50"). Explain trend vs previous term in one sentence
  using only data the tools return.

For syllabus questions, report chapter completion status against the
CBSE/NCERT chapter list - never guess at chapters not returned by the tool.

If data is missing, say so plainly rather than filling a gap."""


def academic_node(state: AgentState) -> dict:
    prompt = SYSTEM_PROMPT.format(
        language=state.get("language", "en"),
        student_id=state.get("student_id") or "unknown",
        tenant_id=state.get("tenant_id") or "unknown",
    )
    messages = [SystemMessage(content=prompt)] + state["messages"]
    return run_tool_loop(llm, messages, ACADEMIC_TOOLS)
