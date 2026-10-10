"""Daily updates and the insight scan, with the LLM parts faked."""

import asyncio
from datetime import date

import pytest
from langchain_core.messages import AIMessage

from app.agents.insight_agent import parse_risk
from app.tasks import daily_batch

TODAY = date(2026, 10, 7)


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    async def fake_send(to, body):
        sent.append(to)
        return {"status": "stubbed"}

    monkeypatch.setattr(daily_batch, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(
        daily_batch, "daily_update_node",
        lambda state: {"messages": [AIMessage(content=f"Update in {state['language']}")]},
    )
    return sent


def test_daily_updates_skip_guardians_without_consent(store, outbox):
    tenant_id = store.default_tenant_id()
    result = asyncio.run(daily_batch.run_daily_updates(tenant_id))

    assert result["students_processed"] == len(store.students(tenant_id))
    assert result["skipped_without_consent"] == ["g-7-1"]
    assert "919000000006" not in outbox
    assert result["messages_sent"] == len(outbox) == len(result["previews"])

    priya = next(p for p in result["previews"] if p["guardian_id"] == "g-3-1")
    assert priya["language"] == "hi" and priya["preview"] == "Update in hi"


def test_failed_whatsapp_send_is_not_counted(store, monkeypatch, outbox):
    async def failing_send(to, body):
        return {"status": "failed", "error": "401"}

    monkeypatch.setattr(daily_batch, "send_whatsapp_message", failing_send)
    result = asyncio.run(daily_batch.run_daily_updates(store.default_tenant_id()))
    assert result["messages_sent"] == 0
    assert result["previews"]


def test_student_summary_lists_recent_absences(store):
    rohan = store.student("s-5-1")
    summary = daily_batch.build_student_summary(rohan, TODAY)
    assert summary["student"] == "Rohan"
    assert [a["date"] for a in summary["absences"]] == ["2026-09-29", "2026-10-01", "2026-10-06"]
    assert summary["school_days_recorded"] > 0
    assert summary["assessments"]


def fake_risk(summary):
    if len(summary["absences"]) >= 3:
        return {"alert_type": "attendance_drop", "severity": "high", "detail": f"{summary['student']} absent 3 times"}
    return {"alert_type": "no_alert", "severity": "low", "detail": ""}


def test_insight_scan_saves_new_alerts_once(store, monkeypatch):
    monkeypatch.setattr(daily_batch, "evaluate_risk", fake_risk)
    tenant_id = store.default_tenant_id()

    first = daily_batch.run_insight_scan(tenant_id, TODAY)
    assert first["saved"] is True
    assert [(a["student_name"], a["alert_type"], a["new"]) for a in first["alerts"]] == [
        ("Rohan", "attendance_drop", True)
    ]
    rows = store.find("insight_alerts", student_id="s-5-1")
    assert len(rows) == 1 and rows[0]["resolved"] == "open"

    second = daily_batch.run_insight_scan(tenant_id, TODAY)
    assert second["alerts"][0]["new"] is False
    assert len(store.find("insight_alerts", student_id="s-5-1")) == 1


def test_parse_risk_handles_bad_output():
    assert parse_risk('{"alert_type": "grade_decline", "severity": "medium", "detail": "x"}')["alert_type"] == "grade_decline"
    assert parse_risk("not json")["alert_type"] == "no_alert"
    assert parse_risk('{"alert_type": "something_else"}')["alert_type"] == "no_alert"
