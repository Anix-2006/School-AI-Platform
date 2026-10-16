"""Consent-approved demo guardians loaded from the school workbook.

Aliases only are written to benchmark artifacts — never full names or phones.
Guardian/student ids are read at runtime from ``data/school_data.xlsx`` so they
are not hard-coded in source (see ``tests/test_no_hardcoded_data.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class TestGuardian:
    """Approved demo identity for load testing."""

    alias: str
    guardian_id: str
    student_id: str
    language: str


def load_approved_guardians(
    workbook_path: str | Path | None = None,
) -> tuple[TestGuardian, ...]:
    """Return consented guardian/student pairs from the workbook."""
    from app.services.excel_store import ExcelStore

    path = Path(workbook_path or settings.school_data_path)
    store = ExcelStore(path)
    approved: list[TestGuardian] = []
    index = 0
    for row in store.rows("guardians"):
        if not row.get("consent_given_at"):
            continue
        guardian_id = str(row.get("id") or "").strip()
        student_id = str(row.get("student_id") or "").strip()
        if not guardian_id or not student_id:
            continue
        language = str(row.get("preferred_language") or "en")
        if language not in {"en", "hi"}:
            language = "en"
        index += 1
        approved.append(
            TestGuardian(
                alias=f"parent-{chr(ord('a') + (index - 1) % 26)}",
                guardian_id=guardian_id,
                student_id=student_id,
                language=language,
            )
        )
    if len(approved) < 1:
        raise RuntimeError(
            f"No consented guardians found in {path}. "
            "Load testing requires demo rows with consent_given_at set."
        )
    return tuple(approved)


def cycle_guardians(
    count: int,
    workbook_path: str | Path | None = None,
) -> list[TestGuardian]:
    if count < 1:
        raise ValueError("need at least one guardian assignment")
    pool = load_approved_guardians(workbook_path)
    return [pool[i % len(pool)] for i in range(count)]
