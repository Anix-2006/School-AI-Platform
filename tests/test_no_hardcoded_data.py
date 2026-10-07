"""No demo names or ids from the workbook may live in app code or the UI."""

import re
from pathlib import Path

from tests.conftest import SOURCE_WORKBOOK

ROOT = SOURCE_WORKBOOK.parent.parent
SHEETS_WITH_PEOPLE = ("tenants", "students", "guardians", "teachers")


def workbook_values():
    from app.services.excel_store import ExcelStore

    store = ExcelStore(SOURCE_WORKBOOK)
    values = set()
    for sheet in SHEETS_WITH_PEOPLE:
        for row in store.rows(sheet):
            for key in ("id", "name", "whatsapp_number", "whatsapp_phone_number_id"):
                if row.get(key):
                    values.add(str(row[key]))
            if sheet != "tenants" and row.get("name"):
                values.update(part for part in str(row["name"]).split() if len(part) > 3)
    return values


def source_files():
    for folder in ("app", "ui"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in {".py", ".html", ".js", ".json"} and "__pycache__" not in path.parts:
                yield path


def test_no_workbook_names_or_ids_in_code():
    values = workbook_values()
    pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True)) + r")(?![\w-])")
    hits = []
    for path in source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = pattern.search(line)
            if match:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: {match.group(0)}")
    assert not hits, "Hardcoded demo data found:\n" + "\n".join(hits)
