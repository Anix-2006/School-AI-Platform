"""Runs as a batch job (see app/tasks/daily_batch.py), not on the
conversational path. Flags at-risk students to management proactively -
this is the differentiator vs a plain notification bot."""

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import settings

llm = ChatOpenAI(model=settings.openai_model_fast, api_key=settings.openai_api_key)

SYSTEM_PROMPT = """You review a student's recent attendance and
assessment data and decide if it warrants a risk alert to school
management. Flag only on clear patterns (e.g. 3+ absences in 2 weeks,
a drop of 15+ marks between terms in the same subject) - do not flag on
single data points or normal variation. Respond with a short factual
alert_type and one-sentence detail, or 'no_alert' if nothing warrants
attention."""


def evaluate_risk(student_summary: dict) -> dict:
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=str(student_summary)),
    ]
    response = llm.invoke(messages)
    return {"raw": response.content}
