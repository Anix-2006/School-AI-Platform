"""Daily batch job: generates and sends the daily update to every active
student's guardian, and runs the insight agent over recent data.
In production, trigger this from Celery beat or a scheduled K8s CronJob,
not the manual endpoint below (kept for local testing/demo)."""

from datetime import date

from app.database import SessionLocal
from app.models.student import Student, Guardian
from app.agents.daily_update_agent import daily_update_node
from app.agents.insight_agent import evaluate_risk
from app.agents.tool_loop import extract_text_reply
from app.services.whatsapp_service import send_whatsapp_message
from langchain_core.messages import HumanMessage


async def run_daily_updates(tenant_id: str):
    db = SessionLocal()
    try:
        students = db.query(Student).filter(Student.tenant_id == tenant_id, Student.active == True).all()  # noqa: E712
        sent = 0
        previews = []
        for student in students:
            guardians = db.query(Guardian).filter(Guardian.student_id == student.id).all()
            for guardian in guardians:
                if not guardian.consent_given_at:
                    continue  # DPDP - never process without recorded consent
                state = {
                    "messages": [HumanMessage(content=f"Give today's update for {student.name}")],
                    "tenant_id": tenant_id,
                    "student_id": student.id,
                    "guardian_id": guardian.id,
                    "language": guardian.preferred_language,
                    "age_tier": student.age_tier.value,
                    "intent": None,
                    "route_to": None,
                }
                result = daily_update_node(state)
                reply = extract_text_reply(result["messages"])
                previews.append({
                    "student_id": student.id,
                    "guardian_id": guardian.id,
                    "preview": (reply or "")[:180],
                })
                if guardian.whatsapp_number:
                    await send_whatsapp_message(to=guardian.whatsapp_number, body=reply)
                    sent += 1
        return {
            "students_processed": len(students),
            "messages_sent": sent,
            "previews": previews,
        }
    finally:
        db.close()


def run_insight_scan(tenant_id: str):
    """Nightly risk scan - kept synchronous/simple here; wire real
    attendance/marks aggregation before production use."""
    db = SessionLocal()
    try:
        students = db.query(Student).filter(Student.tenant_id == tenant_id, Student.active == True).all()  # noqa: E712
        alerts = []
        for student in students:
            summary = {"student_id": student.id, "name": student.name}  # stub - aggregate real data here
            result = evaluate_risk(summary)
            if "no_alert" not in result["raw"].lower():
                alerts.append({"student_id": student.id, "detail": result["raw"]})
        return alerts
    finally:
        db.close()
