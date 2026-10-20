"""Fixed voice phrases by language. Do not inline these strings in the pipeline."""

from __future__ import annotations

COULD_NOT_HEAR: dict[str, str] = {
    "en": "I could not hear that, please say it again.",
    "hi": "मुझे सुनाई नहीं दिया, कृपया फिर से बोलें।",
    "te": "నాకు వినిపించలేదు, దయచేసి మళ్లీ చెప్పండి.",
}

DEFAULT_LANGUAGE = "en"


def phrase(key: str, language: str | None) -> str:
    """Return a fixed phrase in the parent's language, falling back to English."""
    table = {"could_not_hear": COULD_NOT_HEAR}.get(key, COULD_NOT_HEAR)
    code = (language or DEFAULT_LANGUAGE).strip().lower()
    return table.get(code) or table[DEFAULT_LANGUAGE]
