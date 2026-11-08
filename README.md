# COSC-3506 Individual Assignment — Academic Audit API (Phases 1–3)

FastAPI service that ingests a course catalog (Phase 1), a student's transcript and plan (Phase 2),
and produces a graduation audit report (Phase 3).

## Endpoints
| Method | Path | Phase |
|---|---|---|
| POST | `/api/v1/admin/catalog/import` (multipart `file`) | 1 |
| GET | `/api/v1/catalog/courses/{course_code}` | 1 |
| POST | `/api/v1/students/{id}/history/import` (multipart `file`) | 2 |
| PUT / DELETE | `/api/v1/students/{id}/history` | 2 |
| POST / PUT / DELETE | `/api/v1/students/{id}/plan` | 2 |
| GET | `/api/v1/students/{id}/profile` | 2 |
| GET | `/api/v1/students/{id}/audit-report?strict=<bool>` | 3 |

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
