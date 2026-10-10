"""Runs as a batch job (see app/tasks/daily_batch.py), not on the
conversational path. Flags at-risk students to management proactively -
this is the differentiator vs a plain notification bot."""

import json
import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import settings

logger = logging.getLogger(__name__)

llm = ChatOpenAI(
    model=settings.openai_model_fast,
    api_key=settings.openai_api_key,
    temperature=0,
    model_kwargs={"response_format": {"type": "json_object"}},
)

SYSTEM_PROMPT = """You review a student's recent attendance and
assessment data and decide if it warrants a risk alert to school
management. Use only the data given. The rules:
- attendance_drop: absence_count is 3 or more (the window is two weeks).
- grade_decline: in one subject, the percentage drops by 15 points or
  more from an earlier term to a later term (e.g. FA1 70% -> FA2 54%).
- Otherwise no_alert. Do not flag single data points or normal variation.
If both apply, use attendance_drop and mention the marks drop in detail.
Severity: high for 5+ absences or a 25+ point drop, medium for 4
absences or a 20+ point drop, low otherwise.

Respond with JSON only:
{"alert_type": "attendance_drop" | "grade_decline" | "no_alert",
 "severity": "low" | "medium" | "high",
 "detail": "one factual sentence citing the numbers"}
Use alert_type "no_alert" (with an empty detail) when nothing warrants attention."""


ALERT_TYPES = {"attendance_drop", "grade_decline"}
SEVERITIES = {"low", "medium", "high"}
NO_ALERT = {"alert_type": "no_alert", "severity": "low", "detail": ""}


def parse_risk(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        logger.warning("Insight agent returned non-JSON output: %r", raw)
        return dict(NO_ALERT)
    alert_type = str(data.get("alert_type") or "")
    if alert_type not in ALERT_TYPES:
        return dict(NO_ALERT)
    severity = str(data.get("severity") or "").lower()
    return {
        "alert_type": alert_type,
        "severity": severity if severity in SEVERITIES else "medium",
        "detail": str(data.get("detail") or ""),
    }


def evaluate_risk(student_summary: dict) -> dict:
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(student_summary)),
    ]
    response = llm.invoke(messages)
    return parse_risk(response.content)
