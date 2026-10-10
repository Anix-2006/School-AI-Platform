"""CBSE/NCERT curriculum lookup over the workbook's `curriculum` sheet
(one row per chapter: grade, subject, chapter_no, chapter, summary)."""

import re

from app.services.excel_store import get_store
from app.services.text_match import same_text, subject_matches


def search_curriculum(query: str, grade: str, subject: str) -> str:
    chapters = [
        r for r in get_store().rows("curriculum")
        if same_text(r.get("grade"), grade) and subject_matches(subject, r.get("subject"))
    ]
    if not chapters:
        return f"No curriculum content found for grade {grade} {subject}."
    chapters.sort(key=lambda r: r.get("chapter_no") or 0)

    words = {w for w in re.findall(r"[a-z]+", (query or "").lower()) if len(w) > 3}
    matching = [
        r for r in chapters
        if any(w in f"{r.get('chapter', '')} {r.get('summary', '')}".lower() for w in words)
    ]
    return "\n".join(
        f"Chapter {r.get('chapter_no')}: {r['chapter']} - {r.get('summary') or ''}".strip()
        for r in (matching or chapters)
    )
