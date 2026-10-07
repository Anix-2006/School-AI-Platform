"""/students and /guardians read and write the workbook."""

NEW_STUDENT = {"id": "test-4-1", "name": "Test Child", "grade": "4", "section": "A", "age_tier": "primary"}


def test_list_students_comes_from_sheet(client, store):
    res = client.get("/students")
    assert res.status_code == 200
    ids = [s["id"] for s in res.json()]
    assert ids == [s["id"] for s in store.students(store.default_tenant_id())]
    assert "s-5-1" in ids


def test_create_student_appends_row(client, store):
    res = client.post("/students", json={**NEW_STUDENT, "date_of_birth": "2016-05-01"})
    assert res.status_code == 200
    row = store.get("students", "test-4-1")
    assert row["name"] == "Test Child"
    assert row["tenant_id"] == store.default_tenant_id()
    assert row["date_of_birth"] == "2016-05-01"
    assert row["active"] is True
    assert "test-4-1" in [s["id"] for s in client.get("/students").json()]


def test_duplicate_student_id_is_409(client):
    res = client.post("/students", json={**NEW_STUDENT, "id": "s-3-1"})
    assert res.status_code == 409


def test_invalid_age_tier_is_422(client):
    res = client.post("/students", json={**NEW_STUDENT, "age_tier": "college"})
    assert res.status_code == 422


def test_guardians_list_has_child_and_consent(client):
    res = client.get("/guardians")
    assert res.status_code == 200
    by_id = {g["id"]: g for g in res.json()}
    assert by_id["g-3-1"]["student_name"] == "Ananya"
    assert by_id["g-3-1"]["preferred_language"] == "hi"
    assert by_id["g-3-1"]["has_consent"] is True
    assert by_id["g-7-1"]["has_consent"] is False
