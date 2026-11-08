"""COSC-3506 Individual Assignment — Phases 1, 2 and 3 in one FastAPI service."""

import os
from typing import Dict, List

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

import audit_engine as engine

app = FastAPI(title="Academic Audit API", version="3.0.0")


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


# --- IN-MEMORY STORES --------------------------------------------------------
# The catalog is global; student data is strictly isolated per student_id.
catalog_db: Dict[str, Dict] = {}
database: Dict[str, Dict] = {}


def get_student(student_id: str) -> Dict:
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    return database[student_id]


@app.get("/")
def read_root():
    return {"status": "Academic Audit API is running", "phase": 3}


@app.get("/health")
def health():
    return {"status": "ok"}


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


# --- PHASE 2: HISTORY --------------------------------------------------------
@app.post("/api/v1/students/{student_id}/history/import", status_code=201)
async def import_history(student_id: str, file: UploadFile = File(...)):
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


@app.get("/api/v1/students/{student_id}/profile")
def get_profile(student_id: str):
    student = get_student(student_id)
    return {
        "student_id": student_id,
        "history": student["history"],
        "plan": student["plan"],
    }


# --- PHASE 3: AUDIT ----------------------------------------------------------
@app.get("/api/v1/students/{student_id}/audit-report")
def audit_report(student_id: str, strict: bool = Query(False)):
    student = get_student(student_id)
    return engine.build_audit(
        student_id, student["history"], student["plan"], catalog_db, strict
    )


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
