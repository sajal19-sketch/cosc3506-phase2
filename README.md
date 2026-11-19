# COSC-3506 Individual Assignment — Academic Audit API (Phases 1–4)

FastAPI service that ingests a course catalog (Phase 1), a student's transcript and plan (Phase 2),
produces a graduation audit report (Phase 3), and adds JWT auth, BOLA/RBAC, rate limiting and a
prerequisite-aware recommendations engine (Phase 4).

## Endpoints
| Method | Path | Phase |
|---|---|---|
| POST | `/api/v1/admin/catalog/import` (multipart `file`) | 1 |
| GET | `/api/v1/catalog/courses/{course_code}` | 1 |
| POST | `/api/v1/auth/register` `{username, password}` → 201 / 409 | 4 |
| POST | `/api/v1/auth/login` → `{access_token, token_type}` / 401 | 4 |
| POST | `/api/v1/students/{id}/history/import` (multipart `file`) — **owner token only** | 2, 4 |
| PUT / DELETE | `/api/v1/students/{id}/history` | 2 |
| POST / PUT / DELETE | `/api/v1/students/{id}/plan` | 2 |
| GET | `/api/v1/students/{id}/profile` — **owner or admin** | 2, 4 |
| GET | `/api/v1/students/{id}/plan` — **owner or admin** | 4 |
| GET | `/api/v1/students/{id}/audit-report?strict=<bool>` — **10 req/min** | 3, 4 |
| GET | `/api/v1/students/{id}/recommendations?max_per_term=5` — **owner or admin** | 4 |

## Phase 4 security (`security.py`)
- Passwords hashed with bcrypt (random salt per user); only hashes are kept in memory.
- Login issues an HS256 JWT with `sub` (username), `role`, `iat`, `exp` (60 min). Set `JWT_SECRET` in production
  (`render.yaml` generates one); otherwise a random per-process key is used.
- Admin `admin` / `admin` is seeded at startup with `role: "admin"`; registering it again returns 409.
- **BOLA:** `history/import` requires `sub == student_id`. **RBAC:** profile, plan (GET) and recommendations allow the
  owner or an admin. Missing, malformed, expired, forged or mismatched tokens all return **401**.
- **Rate limit:** sliding 60 s window on `audit-report`, keyed by JWT subject (or client IP from `X-Forwarded-For`
  when there is no token). Request 11 in a window → **429** with `Retry-After`.

## Recommendations (`recommender.py`)
- Courses with status Completed or In-Progress (and their cross-listed equivalents) are removed from the catalog;
  remaining cross-listed pairs collapse to one course.
- Remaining courses form a prerequisite DAG. Kahn's algorithm schedules it one BFS layer per term, so a course
  only appears after every prerequisite is completed or placed in an earlier term. Ties go to the longest
  dependency chain (critical path), up to `max_per_term` courses (default 5 = 15 credits).
- Terms run Fall/Winter, starting after the student's latest history term. Prerequisites not in the catalog are
  ignored; courses caught in a prerequisite cycle are listed under `unschedulable`.

## Audit rules (`audit_engine.py`)
- Course codes compared after upper-casing and stripping spaces/hyphens.
- Terms ordered `<yy><season>` with `W < SP < S < F`.
- Each planned course's catalog prerequisites need a **Completed** history entry in a strictly earlier term,
  otherwise `MISSING_PREREQUISITE` (grouped by planned term, chronological).
- Planning a course cross-listed (either direction) with a completed course → `CROSS_LIST_CONFLICT`.
- `total_earned` counts each completed course once (best/later pass wins; non-completed attempts = 0).
- `total_remaining_for_graduation = max(0, 120 − earned − planned)`.

## Run locally
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
uvicorn main:app --reload
```

## Quality gate (same as CI)
```bash
flake8 .
pytest --cov=. --cov-config=.coveragerc --cov-fail-under=80
uvicorn main:app --port 8000 &   # then:
BASE_URL=http://127.0.0.1:8000 pytest integration -v
```

## Grader-style self-test (checks A–E, prints a 100-point table)
```bash
python selftest_phase4.py http://127.0.0.1:8000
python selftest_phase4.py https://cosc3506-phase2-1.onrender.com
```
