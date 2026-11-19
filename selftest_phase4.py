"""Grader-style Phase 4 self-test. Runs checks A–E against a running service.

    python selftest_phase4.py http://127.0.0.1:8000
    python selftest_phase4.py https://your-app.onrender.com

Uses fresh random usernames every run, so it is safe to repeat against a live
deployment (wait 60 s between runs if you reuse the same credential).
"""

import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "tests" / "fixtures" / "sample_catalog.html"
TRANSCRIPT = ROOT / "student-example.html"
if not TRANSCRIPT.exists():
    TRANSCRIPT = ROOT / "tests" / "fixtures" / "sample_transcript.html"


def norm(code):
    return "".join(ch for ch in code.upper() if ch.isalnum())


def main(base):
    http = httpx.Client(base_url=base.rstrip("/"), timeout=90)
    for _ in range(9):  # wake a sleeping Render instance (up to ~90 s)
        try:
            if http.get("/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(10)

    tag = uuid.uuid4().hex[:6]
    sid, other = f"st{tag}", f"ot{tag}"
    pw = "Selftest1!"
    files = lambda: {"file": ("t.html", TRANSCRIPT.read_bytes(), "text/html")}  # noqa: E731
    results = []

    def check(section, name, points, ok):
        results.append((section, name, points, bool(ok)))

    # Setup (Phases 1-3)
    http.post("/api/v1/admin/catalog/import", files={"file": ("c.html", CATALOG.read_bytes(), "text/html")})

    # A. Auth lifecycle (25)
    r = http.post("/api/v1/auth/register", json={"username": sid, "password": pw})
    check("A", "register -> 201", 5, r.status_code == 201)
    r = http.post("/api/v1/auth/register", json={"username": sid, "password": pw})
    check("A", "duplicate -> 4xx", 5, 400 <= r.status_code < 500)
    r = http.post("/api/v1/auth/login", json={"username": sid, "password": pw})
    token = r.json().get("access_token", "") if r.status_code == 200 else ""
    check("A", "login ok -> 200 + JWT", 5, r.status_code == 200 and r.json().get("token_type") == "bearer")
    r = http.post("/api/v1/auth/login", json={"username": sid, "password": "nope"})
    check("A", "login wrong -> 4xx", 5, 400 <= r.status_code < 500)
    check("A", "non-trivial token", 5, token.count(".") == 2 and len(token) > 40)
    own = {"Authorization": f"Bearer {token}"}

    http.post("/api/v1/auth/register", json={"username": other, "password": pw})
    other_tok = http.post("/api/v1/auth/login", json={"username": other, "password": pw}).json()["access_token"]
    stranger = {"Authorization": f"Bearer {other_tok}"}
    http.post("/api/v1/auth/register", json={"username": "admin", "password": "admin"})
    r = http.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    admin = {"Authorization": f"Bearer {r.json().get('access_token', '')}"}

    # B. BOLA on history/import (20)
    url = f"/api/v1/students/{sid}/history/import"
    r = http.post(url, files=files(), headers=own)
    check("B", "own token -> 201", 8, r.status_code == 201)
    check("B", "no token -> 401", 6, http.post(url, files=files()).status_code == 401)
    check("B", "other token -> 401", 6, http.post(url, files=files(), headers=stranger).status_code == 401)
    http.post(f"/api/v1/students/{sid}/plan", json={"planned_courses": [{"course_code": "COSC 4426", "term": "26F"}]})

    # C. RBAC on profile (20)
    url = f"/api/v1/students/{sid}/profile"
    check("C", "no token -> 401", 5, http.get(url).status_code == 401)
    check("C", "wrong student -> 401", 5, http.get(url, headers=stranger).status_code == 401)
    check("C", "owner -> 200", 5, http.get(url, headers=own).status_code == 200)
    check("C", "admin -> 200", 5, http.get(url, headers=admin).status_code == 200)

    # D. Rate limiting (20)
    url = f"/api/v1/students/{sid}/audit-report"
    codes = [http.get(url, headers=own).status_code for _ in range(11)]
    check("D", "requests 1-10 -> 200", 10, codes[:10] == [200] * 10)
    check("D", "request 11 -> 429", 10, codes[10] == 429)

    # E. Recommendations (15)
    url = f"/api/v1/students/{sid}/recommendations"
    check("E", "no token -> 401", 3, http.get(url).status_code == 401)
    r = http.get(url, headers=own)
    body = r.json() if r.status_code == 200 else {}
    pathway = body.get("recommended_pathway")
    check("E", "200 + student_id + pathway list", 4,
          body.get("student_id") == sid and isinstance(pathway, list))
    pathway = pathway or []
    history = http.get(f"/api/v1/students/{sid}/profile", headers=own).json()["history"]
    completed = {norm(h["course_code"]) for h in history if h.get("status") == "Completed"}
    index = {norm(c): i for i, t in enumerate(pathway) for c in t["courses"]}
    check("E", "no completed courses", 4, not (completed & set(index)))
    ordered = True
    for code, i in index.items():
        course = http.get(f"/api/v1/catalog/courses/{code}").json()
        for pre in course.get("prerequisites", []):
            if norm(pre) in index and index[norm(pre)] >= i:
                ordered = False
    check("E", "prereqs in earlier terms", 4, ordered and bool(pathway))

    # Report
    print(f"\nPhase 4 self-test against {base}\n")
    print(f"{'Sec':<4}{'Check':<34}{'Pts':>4}  Result")
    print("-" * 52)
    total = 0
    for section, name, points, ok in results:
        total += points if ok else 0
        print(f"{section:<4}{name:<34}{points:>4}  {'PASS' if ok else 'FAIL'}")
    print("-" * 52)
    print(f"{'TOTAL':<38}{total:>4} / 100")
    print("\nPathway:", pathway)
    return 0 if total == 100 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
