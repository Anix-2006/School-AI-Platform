"""Teacher workspace APIs read the workbook (temp copy)."""


def as_teacher(teacher_id):
    return {"X-Teacher-Id": teacher_id}


def test_list_teachers(client, store):
    res = client.get("/teachers")
    assert res.status_code == 200
    assert {t["id"] for t in res.json()} == {t["id"] for t in store.teachers(store.default_tenant_id())}


def test_default_teacher_is_first_in_workbook(client):
    me = client.get("/teachers/me").json()
    assert me["id"] == "t-1"
    assert me["name"] == "Kavita Nair"
    assert me["role"] == "class_teacher"
    assert me["class_teacher_of"] == ["3-A"]
    assert {(s["subject"], s["grade"]) for s in me["subjects"]} == {("Mathematics", "3"), ("Mathematics", "5")}


def test_workspace_lists(client):
    timetable = client.get("/teachers/me/timetable").json()
    assert timetable
    assert timetable[0]["day_name"] == "Monday"
    assert all(slot["subject"] == "Mathematics" for slot in timetable)

    tasks = client.get("/teachers/me/tasks").json()
    assert [t["status"] for t in tasks][0] == "todo"
    assert {t["id"] for t in tasks} >= {"task-01", "task-04"}

    syllabus = client.get("/teachers/me/syllabus").json()
    assert syllabus and all(r["subject"] == "Mathematics" for r in syllabus)
    assert {r["grade"] for r in syllabus} <= {"3", "5"}

    students = client.get("/teachers/me/students").json()
    assert {s["name"] for s in students} == {"Ananya", "Rohan"}


def test_other_teacher_via_header(client):
    me = client.get("/teachers/me", headers=as_teacher("t-6")).json()
    assert me["name"] == "Rajesh Gupta"
    assert me["class_teacher_of"] == ["5-B"]
    students = client.get("/teachers/me/students", headers=as_teacher("t-6")).json()
    assert {s["name"] for s in students} == {"Ananya", "Rohan"}


def test_mark_task_done_writes_to_workbook(client, store):
    res = client.patch("/teachers/me/tasks/task-01", json={"status": "done"})
    assert res.status_code == 200
    assert res.json()["status"] == "done"
    assert store.get("teacher_tasks", "task-01")["status"] == "done"


def test_cannot_update_another_teachers_task(client):
    res = client.patch("/teachers/me/tasks/task-05", json={"status": "done"})
    assert res.status_code == 404


def test_invalid_task_status_is_422(client):
    res = client.patch("/teachers/me/tasks/task-01", json={"status": "maybe"})
    assert res.status_code == 422


def test_locked_workbook_is_503(client, monkeypatch):
    from app.services import excel_store

    def locked(*_args, **_kwargs):
        raise PermissionError("open in Excel")

    monkeypatch.setattr(excel_store.os, "replace", locked)
    res = client.patch("/teachers/me/tasks/task-01", json={"status": "done"})
    assert res.status_code == 503
    assert "close it in Excel" in res.json()["detail"]


def test_unknown_teacher_is_404(client):
    res = client.get("/teachers/me", headers=as_teacher("no-such-teacher"))
    assert res.status_code == 404


def test_teacher_workspace_page_is_served(client):
    res = client.get("/flows/teacher-workspace")
    assert res.status_code == 200
    assert "Teacher workspace" in res.text
