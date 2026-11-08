"""Integration tests that hit a *running* server over real HTTP.

CI boots `uvicorn main:app` and points BASE_URL at it:
    BASE_URL=http://127.0.0.1:8000 pytest integration
"""

import os
import uuid
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        yield client


@pytest.fixture(scope="module")
def student(http):
    sid = f"it-{uuid.uuid4().hex[:8]}"
    r = http.post(
        "/api/v1/admin/catalog/import",
        files={"file": ("catalog.html", (FIXTURES / "sample_catalog.html").read_bytes(), "text/html")},
    )
    assert r.status_code == 200, r.text
    r = http.post(
        f"/api/v1/students/{sid}/history/import",
        files={"file": ("t.html", (FIXTURES / "sample_transcript.html").read_bytes(), "text/html")},
    )
    assert r.status_code == 201, r.text
    plan = [
        {"course_code": "COSC-4426", "term": "26F"},
        {"course_code": "ITEC 3506", "term": "26F"},
        {"course_code": "COSC-4086", "term": "26W"},
        {"course_code": "COSC-3127", "term": "26W"},
    ]
    r = http.post(f"/api/v1/students/{sid}/plan", json={"planned_courses": plan})
    assert r.status_code == 200, r.text
    return sid


def test_server_is_up(http):
    assert http.get("/health").json() == {"status": "ok"}


def test_audit_schema_and_strict(http, student):
    url = f"/api/v1/students/{student}/audit-report"
    body = http.get(url).json()
    for key in ("student_id", "status", "timeline_validation", "cross_list_violations", "credit_summary"):
        assert key in body
    assert body["status"] == "warning"
    assert http.get(url, params={"strict": "true"}).json()["status"] == "failed"


def test_missing_prerequisite(http, student):
    body = http.get(f"/api/v1/students/{student}/audit-report").json()
    terms = [t["term"] for t in body["timeline_validation"]]
    assert terms == ["26W", "26F"]
    errors = [e for t in body["timeline_validation"] for e in t["errors"]]
    assert {"course_code": "COSC-4426", "type": "MISSING_PREREQUISITE",
            "message": "Missing prerequisite: COSC-3127"} in errors


def test_cross_list_violation(http, student):
    body = http.get(f"/api/v1/students/{student}/audit-report").json()
    assert body["cross_list_violations"][0]["type"] == "CROSS_LIST_CONFLICT"


def test_credit_summary(http, student):
    summary = http.get(f"/api/v1/students/{student}/audit-report").json()["credit_summary"]
    assert summary == {"total_earned": 18, "total_planned": 15, "total_remaining_for_graduation": 87}


def test_profile_shape(http, student):
    body = http.get(f"/api/v1/students/{student}/profile").json()
    assert set(body) == {"student_id", "history", "plan"}
