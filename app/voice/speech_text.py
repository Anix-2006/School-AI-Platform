from __future__ import annotations

import re
from datetime import date

MAX_SPEECH_CHARS = 400
TRUNCATION_NOTICE = "The full answer is on screen."
TRUNCATION_NOTICES = {
    "en": TRUNCATION_NOTICE,
    "hi": "पूरा उत्तर स्क्रीन पर है।",
}
_MONTHS = (
    "January February March April May June July August September October November December"
).split()
_HINDI_MONTHS = (
    "जनवरी फ़रवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर"
).split()


def clean_for_speech(
    text: str, language: str = "en", max_chars: int = MAX_SPEECH_CHARS
) -> str:
    value = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_#>`~•]+", " ", value)
    fa_label = r"एफ ए \1" if language == "hi" else r"formative assessment \1"
    sa_label = r"एस ए \1" if language == "hi" else r"summative assessment \1"
    value = re.sub(r"(?i)\bFA\s*(\d+)\b", fa_label, value)
    value = re.sub(r"(?i)\bSA\s*(\d+)\b", sa_label, value)
    marks_label = r"\1 में से \2" if language == "hi" else r"\1 out of \2"
    value = re.sub(
        r"(?<!\w)(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)(?!\w)",
        marks_label,
        value,
    )
    date_formatter = _speak_date_hi if language == "hi" else _speak_date
    value = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", date_formatter, value)
    value = re.sub(r"[✅❌⭐✓✗→←©®™@|{}[\]<>]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_chars:
        return value

    notice = TRUNCATION_NOTICES.get(language, TRUNCATION_NOTICE)
    content_limit = max(1, max_chars - len(notice) - 2)
    shortened = value[: content_limit + 1]
    boundary = max(shortened.rfind("."), shortened.rfind("?"), shortened.rfind("!"))
    if boundary < content_limit // 2:
        boundary = shortened.rfind(" ", 0, content_limit)
    prefix = shortened[: boundary if boundary > 0 else content_limit].rstrip(" ,;:-.")
    return f"{prefix}. {notice}"[:max_chars]


def _speak_date(match: re.Match[str]) -> str:
    try:
        parsed = date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return match[0]
    return f"{parsed.day}{_ordinal_suffix(parsed.day)} {_MONTHS[parsed.month - 1]} {parsed.year}"


def _speak_date_hi(match: re.Match[str]) -> str:
    try:
        parsed = date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return match[0]
    return f"{parsed.day} {_HINDI_MONTHS[parsed.month - 1]} {parsed.year}"


def _ordinal_suffix(day: int) -> str:
    if 10 < day % 100 < 14:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
