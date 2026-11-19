"""Phase 4: auth, BOLA, RBAC, rate limiting and recommendations (mirrors grader checks A–E)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

import audit_engine as engine
import main
import recommender
import security

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = (FIXTURES / "sample_catalog.html").read_bytes()
TRANSCRIPT = (FIXTURES / "sample_transcript.html").read_bytes()


@pytest.fixture(autouse=True)
def clean_state():
    main.catalog_db.clear()
    main.database.clear()
    security.users_db.clear()
    security.seed_admin()
    main.audit_limiter.reset()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def register(client, username, password="Secret1!"):
    return client.post("/api/v1/auth/register", json={"username": username, "password": password})


def login(client, username, password="Secret1!"):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def bearer(client, username, password="Secret1!"):
    register(client, username, password)
    return {"Authorization": f"Bearer {login(client, username, password).json()['access_token']}"}


def admin_header(client):
    register(client, "admin", "admin")  # grader does this first; 409 is fine
    return {"Authorization": f"Bearer {login(client, 'admin', 'admin').json()['access_token']}"}


def setup_student(client, sid):
    client.post("/api/v1/admin/catalog/import", files={"file": ("c.html", CATALOG, "text/html")})
    headers = bearer(client, sid)
    r = client.post(
        f"/api/v1/students/{sid}/history/import",
        files={"file": ("t.html", TRANSCRIPT, "text/html")},
        headers=headers,
    )
    assert r.status_code == 201
    client.post(
        f"/api/v1/students/{sid}/plan",
        json={"planned_courses": [{"course_code": "COSC 4426", "term": "26F"}]},
    )
    return headers


# --- A. Auth lifecycle -------------------------------------------------------
def test_auth_lifecycle(client):
    r = register(client, "s12345", "MyPass1!")
    assert r.status_code == 201 and r.json() == {"status": "registered"}
    assert 400 <= register(client, "s12345", "other").status_code < 500
    r = login(client, "s12345", "MyPass1!")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and len(body["access_token"]) > 20
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["sub"] == "s12345" and claims["role"] == "student"
    assert login(client, "s12345", "wrong").status_code == 401
    assert login(client, "nobody", "x").status_code == 401


def test_passwords_are_hashed(client):
    register(client, "s1", "plaintext-pw")
    stored = security.users_db["s1"]["password_hash"]
    assert "plaintext-pw" not in stored and stored.startswith("$2")
    assert security.verify_password("x", "not-a-hash") is False


def test_admin_seeded_and_role(client):
    assert register(client, "admin", "admin").status_code == 409
    token = login(client, "admin", "admin").json()["access_token"]
    assert jwt.decode(token, options={"verify_signature": False})["role"] == "admin"


def test_register_validation(client):
    assert client.post("/api/v1/auth/register", json={"username": ""}).status_code == 422


# --- B. BOLA on history/import ----------------------------------------------
def test_bola_history_import(client):
    own = bearer(client, "s1")
    other = bearer(client, "s2")
    files = {"file": ("t.html", TRANSCRIPT, "text/html")}
    url = "/api/v1/students/s1/history/import"
    assert client.post(url, files=files).status_code == 401
    assert client.post(url, files=files, headers=other).status_code == 401
    assert client.post(url, files=files, headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.post(url, files=files, headers={"Authorization": "Basic abc"}).status_code == 401
    r = client.post(url, files=files, headers=own)
    assert r.status_code == 201 and r.json()["past_courses_imported"] == 8


def test_expired_and_forged_tokens_rejected(client):
    setup_student(client, "s1")
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    expired = jwt.encode(
        {"sub": "s1", "role": "student", "exp": past}, security.JWT_SECRET, algorithm="HS256"
    )
    forged = jwt.encode(
        {"sub": "s1", "role": "admin", "exp": past + timedelta(days=1)},
        "wrong-key-for-forgery-tests-0123456789",
        algorithm="HS256",
    )
    for token in (expired, forged):
        r = client.get("/api/v1/students/s1/profile", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


# --- C. RBAC on profile / plan -----------------------------------------------
@pytest.mark.parametrize("path", ["profile", "plan"])
def test_rbac(client, path):
    owner = setup_student(client, "s1")
    stranger = bearer(client, "s2")
    url = f"/api/v1/students/s1/{path}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=stranger).status_code == 401
    assert client.get(url, headers=owner).status_code == 200
    assert client.get(url, headers=admin_header(client)).status_code == 200


def test_get_plan_body(client):
    owner = setup_student(client, "s1")
    body = client.get("/api/v1/students/s1/plan", headers=owner).json()
    assert body == {
        "student_id": "s1",
        "planned_courses": [{"course_code": "COSC 4426", "term": "26F"}],
    }


# --- D. Rate limiting --------------------------------------------------------
def test_audit_rate_limit(client):
    headers = setup_student(client, "s1")
    codes = [
        client.get("/api/v1/students/s1/audit-report", headers=headers).status_code
        for _ in range(11)
    ]
    assert codes == [200] * 10 + [429]
    r = client.get("/api/v1/students/s1/audit-report", headers=headers)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    # A different credential has its own budget.
    assert client.get("/api/v1/students/s1/audit-report", headers=admin_header(client)).status_code == 200


def test_rate_limit_by_ip_without_token(client):
    setup_student(client, "s1")
    xff = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
    codes = [client.get("/api/v1/students/s1/audit-report", headers=xff).status_code for _ in range(11)]
    assert codes[-1] == 429
    assert client.get("/api/v1/students/s1/audit-report").status_code == 200


def test_sliding_window_expires():
    now = [0.0]
    limiter = security.SlidingWindowLimiter(2, 60, clock=lambda: now[0])
    assert limiter.retry_after("k") == 0
    assert limiter.allow("k") and limiter.allow("k") and not limiter.allow("k")
    now[0] = 59.9
    assert not limiter.allow("k")
    now[0] = 60.0
    assert limiter.allow("k")


# --- E. Recommendations ------------------------------------------------------
def _term_index(pathway):
    return {
        engine.normalize_code(code): i
        for i, term in enumerate(pathway)
        for code in term["courses"]
    }


def test_recommendations_endpoint(client):
    owner = setup_student(client, "s1")
    url = "/api/v1/students/s1/recommendations"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=bearer(client, "s2")).status_code == 401
    assert client.get(url, headers=admin_header(client)).status_code == 200
    r = client.get(url, headers=owner)
    assert r.status_code == 200
    body = r.json()
    assert body["student_id"] == "s1"
    assert isinstance(body["recommended_pathway"], list)
    index = _term_index(body["recommended_pathway"])
    # Completed / in-progress courses (and ITEC 3506, cross-listed with completed COSC 3506) excluded.
    assert set(index) == {"COSC4426", "COSC4086"}
    terms = [t["term"] for t in body["recommended_pathway"]]
    assert terms[0] == "26F"  # latest history term is 26SP


def test_prereqs_scheduled_in_earlier_terms():
    catalog = {
        engine.normalize_code(c["course_code"]): c for c in engine.parse_catalog(CATALOG)
    }
    result = recommender.recommend("x", [], catalog, "26F")
    pathway = result["recommended_pathway"]
    index = _term_index(pathway)
    assert len(index) == 9  # 10 courses, COSC/ITEC 3506 collapsed to one
    for key, course in catalog.items():
        if key not in index:
            continue
        for prereq in engine.codes_in(course["prerequisites"]):
            pkey = engine.normalize_code(prereq)
            if pkey in index:
                assert index[pkey] < index[key]
    assert [t["term"] for t in pathway][:3] == ["26F", "27W", "27F"]
    assert all(len(t["courses"]) <= recommender.DEFAULT_MAX_PER_TERM for t in pathway)


def test_term_cap_and_unlimited():
    catalog = {
        f"X{i}": {"course_code": f"X {i}", "prerequisites": [], "cross_listed": []}
        for i in range(1000, 1007)
    }
    capped = recommender.recommend("x", [], catalog, "26F", max_per_term=3)
    assert [len(t["courses"]) for t in capped["recommended_pathway"]] == [3, 3, 1]
    flat = recommender.recommend("x", [], catalog, "26F", max_per_term=None)
    assert len(flat["recommended_pathway"]) == 1


def test_cycle_reported_not_scheduled():
    catalog = {
        "AAAA1000": {"course_code": "AAAA 1000", "prerequisites": ["AAAA 2000"], "cross_listed": []},
        "AAAA2000": {"course_code": "AAAA 2000", "prerequisites": ["AAAA 1000"], "cross_listed": []},
        "BBBB1000": {"course_code": "BBBB 1000", "prerequisites": ["ZZZZ 9999"], "cross_listed": []},
    }
    result = recommender.recommend("x", [], catalog, "26F")
    assert result["recommended_pathway"] == [{"term": "26F", "courses": ["BBBB 1000"]}]
    assert result["unschedulable"] == ["AAAA 1000", "AAAA 2000"]


def test_term_helpers():
    assert recommender.next_regular_term("26F") == "27W"
    assert recommender.next_regular_term("26W") == "26F"
    assert recommender.next_regular_term("26SP") == "26F"
    assert recommender.next_regular_term("99F") == "00W"
    assert recommender.term_for_date(2026, 2) == "26W"
    assert recommender.term_for_date(2026, 11) == "26F"
    assert recommender.start_term([], "27W") == "27W"
    assert recommender.start_term([{"term": "25F"}, {"term": "bad"}], "27W") == "26W"


def test_recommendations_unknown_student(client):
    assert client.get(
        "/api/v1/students/ghost/recommendations", headers=admin_header(client)
    ).status_code == 404
