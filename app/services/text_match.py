"""Loose matching for values the LLM passes to tools ('maths', ' fa1')
against what the school typed in the workbook ('Mathematics', 'FA1')."""

SUBJECT_ALIASES = {
    "math": "mathematics",
    "maths": "mathematics",
    "environmental studies": "evs",
    "environment": "evs",
}


def _norm(value) -> str:
    return " ".join(str(value or "").lower().split())


def same_text(a, b) -> bool:
    return _norm(a) == _norm(b)


def subject_matches(wanted, actual) -> bool:
    w = SUBJECT_ALIASES.get(_norm(wanted), _norm(wanted))
    a = SUBJECT_ALIASES.get(_norm(actual), _norm(actual))
    if not w or not a:
        return False
    return w == a or (len(w) >= 3 and a.startswith(w))
