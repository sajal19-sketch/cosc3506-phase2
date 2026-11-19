"""COSC-3506 Individual Assignment — Phases 1–4 in one FastAPI service."""

import os
from datetime import date
from typing import Dict, List

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import audit_engine as engine
import recommender
import security

app = FastAPI(title="Academic Audit API", version="4.0.0")

AUDIT_RATE_LIMIT = 10
AUDIT_RATE_WINDOW = 60.0
audit_limiter = security.SlidingWindowLimiter(AUDIT_RATE_LIMIT, AUDIT_RATE_WINDOW)


# --- DATA MODELS -------------------------------------------------------------
class CourseRecord(BaseModel):
    course_code: str
    term: str
    credits_earned: int = 0
    status: str = ""


class PlannedCourse(BaseModel):
    course_code: str
    term: str


class PlanInput(BaseModel):
    planned_courses: List[PlannedCourse]


class HistoryInput(BaseModel):
    history: List[CourseRecord]


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


# --- IN-MEMORY STORES --------------------------------------------------------
# The catalog is global; student data is strictly isolated per student_id.
catalog_db: Dict[str, Dict] = {}
database: Dict[str, Dict] = {}

# The hardcoded admin exists from startup, so it works without registering.
security.seed_admin()


def get_student(student_id: str) -> Dict:
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    return database[student_id]


@app.get("/")
def read_root():
    return {"status": "Academic Audit API is running", "phase": 4}


@app.get("/health")
def health():
    return {"status": "ok"}


# --- PHASE 4: AUTH -----------------------------------------------------------
@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: Credentials):
    if not security.register_user(payload.username, payload.password):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    return {"status": "registered"}


@app.post("/api/v1/auth/login")
def login(payload: Credentials):
    user = security.authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = security.create_token(payload.username, user["role"])
    return {"access_token": token, "token_type": "bearer"}


# --- PHASE 1: CATALOG --------------------------------------------------------
@app.post("/api/v1/admin/catalog/import")
async def import_catalog(file: UploadFile = File(...)):
    courses = engine.parse_catalog(await file.read())
    if not courses:
        raise HTTPException(status_code=400, detail="No catalog rows found")
    for course in courses:
        catalog_db[engine.normalize_code(course["course_code"])] = course
    return {
        "message": f"Successfully imported {len(courses)} courses",
        "courses_imported": len(courses),
    }


@app.get("/api/v1/catalog/courses/{course_code}")
def get_course(course_code: str):
    course = engine.lookup(catalog_db, course_code)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


# --- PHASE 2: HISTORY (import is BOLA-locked) --------------------------------
@app.post("/api/v1/students/{student_id}/history/import", status_code=201)
async def import_history(
    student_id: str,
    file: UploadFile = File(...),
    claims: Dict = Depends(security.current_claims),
):
    security.require_owner(claims, student_id)
    history = engine.parse_transcript(await file.read())
    student = database.setdefault(student_id, {"history": [], "plan": []})
    student["history"] = history
    return {"status": "success", "past_courses_imported": len(history)}


@app.put("/api/v1/students/{student_id}/history")
def update_history(student_id: str, payload: HistoryInput):
    student = get_student(student_id)
    student["history"] = [c.model_dump() for c in payload.history]
    return {"status": "success", "message": "Academic history updated successfully"}


@app.delete("/api/v1/students/{student_id}/history")
def delete_history(student_id: str):
    get_student(student_id)["history"] = []
    return {"status": "success"}


# --- PHASE 2: PLAN -----------------------------------------------------------
@app.post("/api/v1/students/{student_id}/plan")
def create_plan(student_id: str, payload: PlanInput):
    student = get_student(student_id)
    student["plan"] = [c.model_dump() for c in payload.planned_courses]
    return {"status": "success", "planned_courses_saved": len(student["plan"])}


@app.put("/api/v1/students/{student_id}/plan")
def update_plan(student_id: str, payload: PlanInput):
    return create_plan(student_id, payload)


@app.delete(
    "/api/v1/students/{student_id}/plan", status_code=status.HTTP_204_NO_CONTENT
)
def delete_plan(student_id: str):
    get_student(student_id)["plan"] = []
    return None


@app.get("/api/v1/students/{student_id}/plan")
def get_plan(student_id: str, claims: Dict = Depends(security.current_claims)):
    security.require_owner_or_admin(claims, student_id)
    student = get_student(student_id)
    return {"student_id": student_id, "planned_courses": student["plan"]}


@app.get("/api/v1/students/{student_id}/profile")
def get_profile(student_id: str, claims: Dict = Depends(security.current_claims)):
    security.require_owner_or_admin(claims, student_id)
    student = get_student(student_id)
    return {
        "student_id": student_id,
        "history": student["history"],
        "plan": student["plan"],
    }


# --- PHASE 3: AUDIT (rate limited in Phase 4) --------------------------------
@app.get("/api/v1/students/{student_id}/audit-report")
def audit_report(request: Request, student_id: str, strict: bool = Query(False)):
    key = security.rate_limit_key(request)
    if not audit_limiter.allow(key):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": f"Rate limit exceeded: {AUDIT_RATE_LIMIT} requests per minute"},
            headers={"Retry-After": str(audit_limiter.retry_after(key))},
        )
    student = get_student(student_id)
    return engine.build_audit(
        student_id, student["history"], student["plan"], catalog_db, strict
    )


# --- PHASE 4: RECOMMENDATIONS ------------------------------------------------
@app.get("/api/v1/students/{student_id}/recommendations")
def recommendations(
    student_id: str,
    max_per_term: int = Query(recommender.DEFAULT_MAX_PER_TERM, ge=1, le=10),
    claims: Dict = Depends(security.current_claims),
):
    security.require_owner_or_admin(claims, student_id)
    student = get_student(student_id)
    today = date.today()
    first = recommender.start_term(
        student["history"], recommender.term_for_date(today.year, today.month)
    )
    return recommender.recommend(
        student_id, student["history"], catalog_db, first, max_per_term
    )


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
