LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "te": "Telugu"}


def language_name(code: str | None) -> str:
    """Agents get the language name, not the code - models don't reliably
    treat 'te' as Telugu."""
    code = (code or "en").strip()
    return LANGUAGE_NAMES.get(code.lower(), code)
