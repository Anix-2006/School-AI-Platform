"""Daily batch job: generates and sends the daily update to every active
student's guardian, and runs the insight agent over recent data.
In production, trigger this from Celery beat or a scheduled K8s CronJob,
not the manual endpoint below (kept for local testing/demo)."""

import logging
from datetime import date, datetime, timedelta

from langchain_core.messages import HumanMessage

from app.agents.daily_update_agent import daily_update_node
from app.agents.insight_agent import evaluate_risk
from app.agents.tool_loop import extract_text_reply
from app.services.excel_store import WorkbookError, get_store
from app.services.whatsapp_service import send_whatsapp_message

logger = logging.getLogger(__name__)

INSIGHT_WINDOW_DAYS = 14


async def run_daily_updates(tenant_id: str):
    store = get_store()
    students = store.students(tenant_id)
    guardians = store.guardians(tenant_id)
    sent = 0
    previews = []
    skipped = []
    for student in students:
        for guardian in (g for g in guardians if g.get("student_id") == student["id"]):
            if not store.has_consent(guardian):
                skipped.append(guardian["id"])
                continue  # DPDP - never process without recorded consent
            state = {
                "messages": [HumanMessage(content=f"Give today's update for {student['name']}")],
                "tenant_id": tenant_id,
                "student_id": student["id"],
                "guardian_id": guardian["id"],
                "language": guardian.get("preferred_language") or "en",
                "age_tier": student.get("age_tier"),
                "intent": None,
                "route_to": None,
            }
            result = daily_update_node(state)
            reply = extract_text_reply(result["messages"])
            previews.append({
                "student_id": student["id"],
                "guardian_id": guardian["id"],
                "language": state["language"],
                "preview": (reply or "")[:180],
            })
            if guardian.get("whatsapp_number"):
                delivery = await send_whatsapp_message(to=guardian["whatsapp_number"], body=reply)
                if delivery.get("status") != "failed":
                    sent += 1
    return {
        "students_processed": len(students),
        "messages_sent": sent,
        "skipped_without_consent": skipped,
        "previews": previews,
    }


def build_student_summary(student: dict, today: date | None = None) -> dict:
    """What the insight agent sees: absences in the last two weeks and
    marks/remarks per subject per term, straight from the workbook."""
    store = get_store()
    today = today or date.today()
    start = (today - timedelta(days=INSIGHT_WINDOW_DAYS - 1)).isoformat()
    attendance = [
        r for r in store.find("attendance", student_id=student["id"])
        if start <= (r.get("date") or "") <= today.isoformat()
    ]
    assessments: dict[str, dict[str, str]] = {}
    for r in store.find("assessment_records", student_id=student["id"]):
        if r.get("marks_obtained") is not None and r.get("marks_total"):
            percent = round(100 * r["marks_obtained"] / r["marks_total"])
            result = f"{r['marks_obtained']}/{r['marks_total']} ({percent}%)"
        else:
            result = r.get("qualitative_remark") or "no result"
        assessments.setdefault(r["subject"], {})[r["term"]] = result
    absences = [
        {"date": r["date"], "note": r.get("note")}
        for r in sorted(attendance, key=lambda r: r["date"]) if r.get("present") is False
    ]
    return {
        "student": student["name"],
        "grade": student["grade"],
        "section": student.get("section"),
        "attendance_window": f"{start} to {today.isoformat()}",
        "school_days_recorded": len(attendance),
        "absence_count": len(absences),
        "absences": absences,
        "assessments": assessments,
    }


def run_insight_scan(tenant_id: str, today: date | None = None) -> dict:
    """Nightly risk scan. New alerts are written to the insight_alerts sheet;
    an alert type that is still open for a student isn't duplicated."""
    store = get_store()
    open_alerts = {
        (a.get("student_id"), a.get("alert_type"))
        for a in store.find("insight_alerts", tenant_id=tenant_id)
        if (a.get("resolved") or "open") != "resolved"
    }
    now = datetime.now().isoformat(timespec="seconds")
    alerts, new_rows = [], []
    for student in store.students(tenant_id):
        risk = evaluate_risk(build_student_summary(student, today))
        if risk["alert_type"] == "no_alert":
            continue
        is_new = (student["id"], risk["alert_type"]) not in open_alerts
        alerts.append({
            "student_id": student["id"],
            "student_name": student["name"],
            **risk,
            "new": is_new,
        })
        if is_new:
            new_rows.append({
                "tenant_id": tenant_id,
                "student_id": student["id"],
                "alert_type": risk["alert_type"],
                "severity": risk["severity"],
                "detail": risk["detail"],
                "created_at": now,
                "resolved": "open",
            })
    saved = True
    try:
        store.append_rows("insight_alerts", new_rows)
    except WorkbookError as exc:
        logger.warning("Insight alerts not saved: %s", exc)
        saved = False
    return {"alerts": alerts, "saved": saved}
