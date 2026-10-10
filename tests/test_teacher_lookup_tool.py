"""get_teachers_for_student tool, using the demo workbook (temp copy)."""

from app.agents import tools

ANANYA = "s-3-1"


def lookup(**args):
    return tools.get_teachers_for_student.invoke(args)


def test_returns_class_and_subject_teachers():
    assert lookup(student_id=ANANYA) == {
        "teachers": [
            {"name": "Kavita Nair", "class_teacher": True, "subjects": ["Mathematics"]},
            {"name": "Rajesh Gupta", "class_teacher": False, "subjects": ["English"]},
            {"name": "Sunita Joshi", "class_teacher": False, "subjects": ["EVS"]},
        ]
    }


def test_subject_filter_keeps_class_teacher():
    assert lookup(student_id=ANANYA, subject="english") == {
        "teachers": [
            {"name": "Kavita Nair", "class_teacher": True, "subjects": []},
            {"name": "Rajesh Gupta", "class_teacher": False, "subjects": ["English"]},
        ]
    }


def test_subject_aliases_match():
    teachers = lookup(student_id=ANANYA, subject="Maths")["teachers"]
    assert teachers == [{"name": "Kavita Nair", "class_teacher": True, "subjects": ["Mathematics"]}]


def test_subject_with_no_teacher_returns_class_teacher_and_note():
    assert lookup(student_id=ANANYA, subject="Hindi") == {
        "teachers": [{"name": "Kavita Nair", "class_teacher": True, "subjects": []}],
        "note": "No Hindi teacher is assigned to this student. "
        "The class teacher does not teach this subject.",
    }


def test_student_without_assignments_gets_empty_list(store):
    store.append_row("students", {
        "id": "test-new", "tenant_id": store.default_tenant_id(), "name": "New Child",
        "grade": "4", "section": "C", "age_tier": "primary", "active": True,
    })
    assert lookup(student_id="test-new") == {"teachers": []}


def test_unknown_student():
    assert lookup(student_id="no-such-student") == {"error": "student not found"}


def test_inactive_teachers_are_hidden(store):
    rajesh = next(t for t in store.rows("teachers") if t["name"] == "Rajesh Gupta")
    store.update_row("teachers", rajesh["id"], {"active": False})

    names = [t["name"] for t in lookup(student_id=ANANYA)["teachers"]]
    assert names == ["Kavita Nair", "Sunita Joshi"]


def test_no_contact_details_are_returned():
    for entry in lookup(student_id=ANANYA)["teachers"]:
        assert set(entry) == {"name", "class_teacher", "subjects"}


def test_tool_is_bound_only_to_parent_teacher_agent():
    other_agents = (
        tools.DAILY_UPDATE_TOOLS,
        tools.ACADEMIC_TOOLS,
        tools.COMMUNICATION_TOOLS,
        tools.INSIGHT_TOOLS,
    )
    assert tools.get_teachers_for_student in tools.PARENT_TEACHER_TOOLS
    assert all(tools.get_teachers_for_student not in group for group in other_agents)
