"""Export saved parent/assistant turns to JSONL for offline evaluation.

Reads the conversations and messages sheets through get_store(). From the
repo root:

    python scripts/export_parent_teacher_turns.py
    python scripts/export_parent_teacher_turns.py --since 2026-10-01
    python scripts/export_parent_teacher_turns.py --agent academic

The default file is data/exports/parent_teacher_turns.jsonl. That directory
is gitignored. Exports can contain sensitive student information and are
not uploaded anywhere.

--since is an inclusive calendar date (YYYY-MM-DD). A turn is kept when the
parent message's created_at falls on or after that date. If the parent row
has no timestamp, the assistant timestamp is used. Turns with neither
timestamp are omitted when --since is set.

Each parent message is paired with the next assistant reply in the same
conversation. The pair is exported only when that reply's agent equals
--agent (default parent_teacher). A reply is used at most once.

Guardian phone numbers, WhatsApp numbers, emails, and the guardian's name
are removed from the question and reply text. Everyday words that happen
to be a one-word name are left in place so the evaluation text stays useful.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.excel_store import WorkbookError, get_store  # noqa: E402

DEFAULT_OUTPUT = ROOT / "data" / "exports" / "parent_teacher_turns.jsonl"
DEFAULT_AGENT = "parent_teacher"
PUBLIC_FIELDS = (
    "input",
    "reply",
    "route",
    "intent_source",
    "intent_confidence",
    "student_id",
    "tenant_id",
    "created_at",
)
_CONTACT_KEYS = ("whatsapp_number", "phone_number", "email")
_PHONE_IN_TEXT = re.compile(r"(?<!\d)\+?\d[\d\s\-()]{8,}\d")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A guardian whose whole name is one of these everyday words is not stripped
# from the sentence. The full multi-word name is still removed.
_ORDINARY_WORDS = frozenset({
    "will", "mark", "may", "june", "april", "hope", "grace", "faith",
    "rose", "joy", "bill", "page", "frank", "chase", "rich", "art",
    "amber", "summer", "autumn", "class", "today", "teacher", "school",
})


def parse_since(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--since must be a calendar date YYYY-MM-DD") from exc


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _sort_key(message: dict) -> tuple:
    # Sheet order breaks ties. Message ids are random, so they are not a sequence.
    return (_text(message.get("created_at")), message.get("_seq", 0))


def _turn_day(user: dict, assistant: dict) -> str:
    stamp = _text(user.get("created_at")) or _text(assistant.get("created_at"))
    return stamp[:10]


def _confidence(user: dict, assistant: dict):
    for row in (user, assistant):
        value = row.get("intent_confidence")
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _meta(user: dict, assistant: dict, field: str) -> str:
    primary = _text(user.get(field))
    if primary:
        return primary
    return _text(assistant.get(field))


def _guardian_privacy(store, conversation: dict) -> tuple[list[str], str]:
    """Contact values and the guardian name for this conversation.

    Lookup failures return nothing. The row itself is never logged.
    """
    guardian_id = conversation.get("guardian_id")
    if not guardian_id:
        return [], ""
    tenant_id = conversation.get("tenant_id")
    guardian = None
    try:
        guardian = store.guardian(guardian_id, tenant_id)
    except Exception:  # noqa: BLE001 - a lookup problem must not leak the row
        guardian = None
    if not guardian:
        return [], ""
    secrets = []
    for key in _CONTACT_KEYS:
        value = _text(guardian.get(key))
        if len(value) >= 6:
            secrets.append(value)
    return secrets, _text(guardian.get("name"))


def _name_phrases(name: str) -> list[str]:
    cleaned = " ".join(_text(name).split())
    if len(cleaned) < 3 or not any(ch.isalpha() for ch in cleaned):
        return []
    phrases = []
    if " " in cleaned or cleaned.casefold() not in _ORDINARY_WORDS:
        phrases.append(cleaned)
    for part in cleaned.split():
        if len(part) < 4 or not any(ch.isalpha() for ch in part):
            continue
        if part.casefold() in _ORDINARY_WORDS:
            continue
        phrases.append(part)
    ordered = []
    seen = set()
    for phrase in sorted(phrases, key=len, reverse=True):
        key = phrase.casefold()
        if key not in seen:
            seen.add(key)
            ordered.append(phrase)
    return ordered


def _redact(text: str, secrets: list[str], name: str = "") -> str:
    cleaned = text
    for secret in secrets:
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, "[redacted]")
    for phrase in _name_phrases(name):
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        cleaned = pattern.sub("[redacted]", cleaned)
    return _PHONE_IN_TEXT.sub(_redact_phone_match, cleaned)


def _redact_phone_match(match: re.Match) -> str:
    """Mask phone-like text, but leave a bare ISO date alone.

    ``2026-10-06`` fits the digit-and-separator pattern, and so do numbers
    such as ``+91-90000-00088``. Only an exact ``YYYY-MM-DD`` token is kept.
    """
    token = match.group(0).strip()
    if _ISO_DATE.fullmatch(token):
        return match.group(0)
    return "[redacted]"


def pair_turns(store, agent: str, since: str | None = None) -> list[dict]:
    """Parent/assistant pairs for one agent, oldest turn first."""
    conversations = {row.get("id"): row for row in store.rows("conversations") if row.get("id")}
    by_conversation: dict[str, list[dict]] = {}
    for sequence, message in enumerate(store.rows("messages")):
        conversation_id = message.get("conversation_id")
        if conversation_id not in conversations:
            continue
        stamped = dict(message)
        stamped["_seq"] = sequence
        by_conversation.setdefault(conversation_id, []).append(stamped)

    records = []
    for conversation_id, messages in by_conversation.items():
        conversation = conversations[conversation_id]
        secrets, guardian_name = _guardian_privacy(store, conversation)
        ordered = sorted(messages, key=_sort_key)
        index = 0
        while index < len(ordered):
            current = ordered[index]
            if _text(current.get("role")).lower() != "user":
                index += 1
                continue
            reply_at = None
            for later in range(index + 1, len(ordered)):
                role = _text(ordered[later].get("role")).lower()
                if role == "user":
                    break
                if role == "assistant":
                    reply_at = later
                    break
            if reply_at is None:
                index += 1
                continue
            assistant = ordered[reply_at]
            index = reply_at + 1
            if _text(assistant.get("agent")) != agent:
                continue
            question = _redact(_text(current.get("content")), secrets, guardian_name)
            answer = _redact(_text(assistant.get("content")), secrets, guardian_name)
            if not question or not answer:
                continue
            day = _turn_day(current, assistant)
            if since and (not day or day < since):
                continue
            created_at = _text(current.get("created_at")) or _text(assistant.get("created_at"))
            record = {
                "input": question,
                "reply": answer,
                "route": agent,
                "intent_source": _meta(current, assistant, "intent_source"),
                "intent_confidence": _confidence(current, assistant),
                "student_id": _meta(current, assistant, "student_id"),
                "tenant_id": _text(conversation.get("tenant_id")),
                "created_at": created_at,
            }
            records.append((created_at, conversation_id, _text(current.get("id")), record))
    records.sort(key=lambda item: item[:3])
    return [item[3] for item in records]


def write_jsonl(records: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({field: record[field] for field in PUBLIC_FIELDS}, ensure_ascii=False) for record in records]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def export_turns(agent: str = DEFAULT_AGENT, since: str | None = None, output: Path | None = None) -> int:
    destination = Path(output) if output else DEFAULT_OUTPUT
    try:
        records = pair_turns(get_store(), agent, since)
    except WorkbookError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    write_jsonl(records, destination)
    print(f"exported {len(records)} turns", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export parent/assistant turns to JSONL.")
    parser.add_argument("--since", type=parse_since, default=None, help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--agent", default=DEFAULT_AGENT, help="Assistant agent to export (default parent_teacher).")
    parser.add_argument("--output", type=Path, default=None, help="JSONL path. Defaults to data/exports/parent_teacher_turns.jsonl.")
    args = parser.parse_args(argv)
    agent = (args.agent or "").strip()
    if not agent:
        print("error: --agent must be a non-empty agent name", file=sys.stderr)
        return 2
    return export_turns(agent=agent, since=args.since, output=args.output)


if __name__ == "__main__":
    sys.exit(main())
