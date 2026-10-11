"""One parent turn through the orchestrator, shared by the app chat API
and the WhatsApp webhook. The guardian's row in the workbook decides the
student, language and consent - never the caller."""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.messages import HumanMessage

from app.agents.orchestrator import orchestrator_graph
from app.agents.tool_loop import extract_text_reply
from app.services.excel_store import WorkbookError, get_store

logger = logging.getLogger(__name__)


class ChatAccessError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def resolve_parent(tenant_id: str, guardian_id: str, student_id: str | None = None) -> tuple[dict, dict]:
    """Return (guardian, student) or raise ChatAccessError."""
    store = get_store()
    guardian = store.guardian(guardian_id, tenant_id)
    student = store.student(guardian["student_id"], tenant_id) if guardian else None
    if not guardian or not student:
        raise ChatAccessError(404, f"Unknown guardian '{guardian_id}'.")
    if not store.has_consent(guardian):
        raise ChatAccessError(
            403, f"{guardian['name']} has not given consent yet, so the assistant can't reply."
        )
    if student_id and student_id != student["id"]:
        raise ChatAccessError(403, f"Guardian '{guardian_id}' is not linked to student '{student_id}'.")
    return guardian, student


def handle_parent_message(
    tenant_id: str,
    guardian_id: str,
    message: str,
    *,
    student_id: str | None = None,
    session_id: str | None = None,
    channel: str,
    language_override: str | None = None,
) -> dict:
    """Resolve a parent and run one message through the orchestrator."""
    guardian, student = resolve_parent(tenant_id, guardian_id, student_id)
    thread_id = f"{guardian['id']}:{session_id}" if session_id else guardian["id"]
    return run_parent_turn(
        tenant_id, guardian, student, message, channel, thread_id, language_override
    )


def run_parent_turn(
    tenant_id: str,
    guardian: dict,
    student: dict,
    message: str,
    channel: str,
    thread_id: str,
    language_override: str | None = None,
) -> dict:
    language = language_override or guardian.get("preferred_language") or "en"
    state = {
        "messages": [HumanMessage(content=message)],
        "tenant_id": tenant_id,
        "student_id": student["id"],
        "guardian_id": guardian["id"],
        "language": language,
        "reply_language": language if channel == "voice" else None,
        "age_tier": student.get("age_tier"),
        "channel": "voice" if channel == "voice" else "chat",
        "intent": None,
        "route_to": None,
        "router_ms": None,
        "agent_llm_ms": None,
    }
    result = orchestrator_graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
    reply = extract_text_reply(result["messages"])
    agent = result.get("route_to")
    log_turn(tenant_id, guardian["id"], channel, message, reply, agent)
    return {
        "reply": reply,
        "agent_used": agent,
        "intent_source": result.get("intent_source"),
        "intent_confidence": result.get("intent_confidence"),
        "router_ms": result.get("router_ms", 0),
        "agent_llm_ms": result.get("agent_llm_ms", 0),
        "student_id": student["id"],
        "language": language,
    }


def log_turn(tenant_id: str, guardian_id: str, channel: str, message: str, reply: str, agent: str | None) -> None:
    """Save the parent message and the reply to the conversations/messages
    sheets. A failed save (e.g. workbook open in Excel) is logged, not raised:
    the parent still gets their answer."""
    store = get_store()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        conversation = next(
            (
                c for c in store.find("conversations", tenant_id=tenant_id, guardian_id=guardian_id)
                if c.get("channel") == channel
            ),
            None,
        ) or store.append_row(
            "conversations",
            {"tenant_id": tenant_id, "guardian_id": guardian_id, "channel": channel, "created_at": now},
        )
        store.append_rows("messages", [
            {"conversation_id": conversation["id"], "role": "user", "content": message, "created_at": now},
            {"conversation_id": conversation["id"], "role": "assistant", "content": reply,
             "agent": agent, "created_at": now},
        ])
    except WorkbookError as exc:
        logger.warning("Chat turn for %s not saved: %s", guardian_id, exc)
