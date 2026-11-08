from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = (FIXTURES / "sample_catalog.html").read_bytes()
TRANSCRIPT = (FIXTURES / "sample_transcript.html").read_bytes()


@pytest.fixture(autouse=True)
def clean_state():
    main.catalog_db.clear()
    main.database.clear()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def load(client, sid="770001"):
    r = client.post(
        "/api/v1/admin/catalog/import",
        files={"file": ("catalog.html", CATALOG, "text/html")},
    )
    assert r.status_code == 200
    r = client.post(
        f"/api/v1/students/{sid}/history/import",
        files={"file": ("t.html", TRANSCRIPT, "text/html")},
    )
    assert r.status_code == 201
    return r


def test_root_and_health(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_catalog_import_and_lookup(client):
    load(client)
    for code in ("COSC3506", "cosc-3506", "COSC 3506"):
        r = client.get(f"/api/v1/catalog/courses/{code}")
        assert r.status_code == 200
        assert r.json()["cross_listed"] == ["ITEC-3506"]
    assert client.get("/api/v1/catalog/courses/NOPE1234").status_code == 404


def test_catalog_import_rejects_empty(client):
    r = client.post(
        "/api/v1/admin/catalog/import",
        files={"file": ("x.html", b"<html></html>", "text/html")},
    )
    assert r.status_code == 400


def test_history_lifecycle(client):
    r = load(client, "s1")
    assert r.json() == {"status": "success", "past_courses_imported": 8}
    new_history = [
        {"course_code": "COSC-1046", "term": "23F", "credits_earned": 3, "status": "Completed"}
    ]
    r = client.put("/api/v1/students/s1/history", json={"history": new_history})
    assert r.status_code == 200
    assert client.get("/api/v1/students/s1/profile").json()["history"] == new_history
    assert client.delete("/api/v1/students/s1/history").status_code == 200
    assert client.get("/api/v1/students/s1/profile").json()["history"] == []


def test_unknown_student_404(client):
    body = {"planned_courses": [{"course_code": "COSC-3506", "term": "26F"}]}
    assert client.put("/api/v1/students/x/history", json={"history": []}).status_code == 404
    assert client.delete("/api/v1/students/x/history").status_code == 404
    assert client.post("/api/v1/students/x/plan", json=body).status_code == 404
    assert client.put("/api/v1/students/x/plan", json=body).status_code == 404
    assert client.delete("/api/v1/students/x/plan").status_code == 404
    assert client.get("/api/v1/students/x/profile").status_code == 404
    assert client.get("/api/v1/students/x/audit-report").status_code == 404


def test_plan_lifecycle_and_profile(client):
    load(client, "s2")
    plan = [{"course_code": "COSC-3506", "term": "26F"}]
    r = client.post("/api/v1/students/s2/plan", json={"planned_courses": plan})
    assert r.json() == {"status": "success", "planned_courses_saved": 1}
    profile = client.get("/api/v1/students/s2/profile").json()
    assert set(profile) == {"student_id", "history", "plan"}
    assert profile["plan"] == plan
    plan2 = plan + [{"course_code": "COSC-4426", "term": "27W"}]
    r = client.put("/api/v1/students/s2/plan", json={"planned_courses": plan2})
    assert r.json()["planned_courses_saved"] == 2
    assert client.delete("/api/v1/students/s2/plan").status_code == 204
    assert client.get("/api/v1/students/s2/profile").json()["plan"] == []


def test_wrong_verb_rejected(client):
    assert client.get("/api/v1/students/s/history/import").status_code in (404, 405)
    assert client.patch("/api/v1/students/s/plan", json={}).status_code in (404, 405)


def test_student_isolation(client):
    load(client, "a")
    client.post(
        "/api/v1/students/b/history/import",
        files={"file": ("t.html", b"<table></table>", "text/html")},
    )
    assert len(client.get("/api/v1/students/a/profile").json()["history"]) == 8
    assert client.get("/api/v1/students/b/profile").json()["history"] == []


def test_audit_report_full(client):
    load(client)
    plan = [
        {"course_code": "COSC 4426", "term": "26F"},
        {"course_code": "ITEC-3506", "term": "26F"},
        {"course_code": "COSC-4086", "term": "26W"},
        {"course_code": "COSC-3127", "term": "26W"},
    ]
    client.post("/api/v1/students/770001/plan", json={"planned_courses": plan})
    r = client.get("/api/v1/students/770001/audit-report")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "student_id",
        "status",
        "timeline_validation",
        "cross_list_violations",
        "credit_summary",
    }
    assert body["status"] == "warning"
    assert [t["term"] for t in body["timeline_validation"]] == ["26W", "26F"]
    assert body["timeline_validation"][1]["errors"][0]["message"] == (
        "Missing prerequisite: COSC-3127"
    )
    assert body["cross_list_violations"][0]["course_code"] == "ITEC-3506"
    assert body["credit_summary"] == {
        "total_earned": 18,
        "total_planned": 15,
        "total_remaining_for_graduation": 87,
    }
    strict = client.get("/api/v1/students/770001/audit-report?strict=true").json()
    assert strict["status"] == "failed"
    relaxed = client.get("/api/v1/students/770001/audit-report?strict=false").json()
    assert relaxed["status"] == "warning"


def test_audit_report_ok(client):
    load(client)
    client.post(
        "/api/v1/students/770001/plan",
        json={"planned_courses": [{"course_code": "COSC-3127", "term": "26F"}]},
    )
    body = client.get("/api/v1/students/770001/audit-report?strict=true").json()
    assert body["status"] == "ok"
