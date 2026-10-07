"""Agent tools read the workbook through get_store()."""

from app.agents import tools

ROHAN = "s-5-1"
ANANYA = "s-3-1"


def call(tool, **args):
    return tool.invoke(args)


def tenant(store):
    return store.default_tenant_id()


def test_student_profile():
    assert call(tools.get_student_profile, student_id=ANANYA) == {
        "name": "Ananya", "grade": "3", "section": "A", "age_tier": "primary",
    }
    assert call(tools.get_student_profile, student_id="nope") == {"error": "student not found"}


def test_attendance_change_in_workbook_is_seen_without_restart(store):
    assert call(tools.get_attendance, student_id=ROHAN, for_date="2026-10-06")["present"] is False

    row = next(r for r in store.find("attendance", student_id=ROHAN) if r["date"] == "2026-10-06")
    store.update_row("attendance", row["id"], {"present": True, "note": None})

    assert call(tools.get_attendance, student_id=ROHAN, for_date="2026-10-06") == {"present": True, "note": None}


def test_attendance_without_record():
    assert call(tools.get_attendance, student_id=ROHAN, for_date="2030-01-01") == {
        "present": None, "note": "no record",
    }


def test_daily_diary():
    entry = call(tools.get_daily_diary, student_id=ANANYA, for_date="2026-10-06")
    assert entry.get("homework") or entry.get("care_notes")
    assert call(tools.get_daily_diary, student_id=ANANYA, for_date="2026-01-01") == {
        "error": "no entry for this date",
    }


def test_assessment_term_filter():
    every = call(tools.get_assessment_records, student_id=ANANYA)
    assert every
    term = every[0]["term"]
    filtered = call(tools.get_assessment_records, student_id=ANANYA, term=term.lower())
    assert filtered and all(r["term"] == term for r in filtered)


def test_syllabus_progress(store):
    rows = call(tools.get_syllabus_progress, tenant_id=tenant(store), grade="3", subject="maths")
    assert rows and all({"chapter", "status", "planned_date"} <= set(r) for r in rows)


def test_curriculum_search():
    text = call(tools.search_cbse_curriculum, query="what is she learning", grade="3", subject="Mathematics")
    assert text.startswith("Chapter ")
    assert "No curriculum content" in call(
        tools.search_cbse_curriculum, query="x", grade="99", subject="Mathematics"
    )


def test_school_calendar_finds_annual_day(store):
    result = call(tools.get_school_calendar, tenant_id=tenant(store), from_date="2026-10-07")
    annual = [e for e in result["events"] if e["title"] == "Annual Day"]
    assert annual and annual[0]["start_date"] == "2026-12-12"
    assert [e["start_date"] for e in result["events"]] == sorted(e["start_date"] for e in result["events"])


def test_school_calendar_includes_events_that_overlap_the_range(store):
    result = call(tools.get_school_calendar, tenant_id=tenant(store), from_date="2026-10-20", to_date="2026-10-20")
    assert [e["title"] for e in result["events"]] == ["Dussehra break"]


def test_school_calendar_empty_range_has_note(store):
    result = call(tools.get_school_calendar, tenant_id=tenant(store), from_date="2030-01-01", to_date="2030-01-02")
    assert result["events"] == [] and "note" in result


def test_school_info_by_topic(store):
    fees = call(tools.get_school_info, tenant_id=tenant(store), topic="fees")["info"]
    assert [i["topic"] for i in fees] == ["fees"]
    everything = call(tools.get_school_info, tenant_id=tenant(store), topic="zzz")["info"]
    assert len(everything) == len(store.find("school_info", tenant_id=tenant(store)))


def test_calendar_and_info_tools_only_for_communication_agent():
    for t in (tools.get_school_calendar, tools.get_school_info):
        assert t in tools.COMMUNICATION_TOOLS
        for group in (tools.DAILY_UPDATE_TOOLS, tools.ACADEMIC_TOOLS, tools.PARENT_TEACHER_TOOLS, tools.INSIGHT_TOOLS):
            assert t not in group
