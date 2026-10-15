from fastapi import FastAPI, HTTPException, status, UploadFile, File
from pydantic import BaseModel
from typing import List, Dict
from bs4 import BeautifulSoup

app = FastAPI()

# --- DATA MODELS ---
class CourseRecord(BaseModel):
    course_code: str
    term: str
    credits_earned: int = 0
    status: str = ""

class PlanInput(BaseModel):
    planned_courses: List[CourseRecord]

class HistoryInput(BaseModel):
    history: List[CourseRecord]

class StudentProfile(BaseModel):
    student_id: str
    history: List[CourseRecord]
    plan: List[CourseRecord]

# --- IN-MEMORY DATABASE ---
# Strict data isolation: { "123": {"history": [...], "plan": [...]}, "456": {...} }
database: Dict[str, Dict] = {}

# --- HELPER FOR GRADES ---
def get_grade_weight(grade_str: str) -> int:
    grade = grade_str.strip()
    if not grade or grade == 'P':
        return 0
    try:
        return int(grade) + 100  # Numeric grades beat letters
    except ValueError:
        return 100 - ord(grade[0].upper()) if grade else 0

# --- HISTORY ENDPOINTS (HTML IN) ---
@app.post("/api/v1/students/{student_id}/history/import", status_code=201)
async def import_history(student_id: str, file: UploadFile = File(...)):
    content = await file.read()
    soup = BeautifulSoup(content, 'html.parser')
    
    parsed_courses = {}
    
    for tr in soup.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) >= 6:
            status_val = tds[0].get_text(strip=True)
            course = tds[1].get_text(strip=True)
            grade = tds[3].get_text(strip=True)
            term = tds[4].get_text(strip=True)
            credits_str = tds[5].get_text(strip=True)
            
            if status_val in ['Completed', 'In-Progress', 'Attempted'] and term:
                try:
                    credits = int(credits_str)
                except ValueError:
                    credits = 0
                
                key = (course, term)
                weight = get_grade_weight(grade)
                
                # Deduplication logic
                if key not in parsed_courses:
                    parsed_courses[key] = {"status": status_val, "course": course, "grade": grade, "term": term, "credits": credits, "weight": weight}
                else:
                    existing = parsed_courses[key]
                    if weight > existing["weight"] or (weight == existing["weight"] and credits > existing["credits"]):
                        parsed_courses[key] = {"status": status_val, "course": course, "grade": grade, "term": term, "credits": credits, "weight": weight}

    # Initialize the student
    if student_id not in database:
        database[student_id] = {"history": [], "plan": []}
        
    history_list = [
        {"course_code": v["course"], "term": v["term"], "credits_earned": v["credits"], "status": v["status"]}
        for v in parsed_courses.values()
    ]
    
    database[student_id]["history"] = history_list
    
    return {"status": "success", "past_courses_imported": len(history_list)}

@app.put("/api/v1/students/{student_id}/history")
def update_history(student_id: str, history_data: HistoryInput):
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    
    database[student_id]["history"] = [course.model_dump() for course in history_data.history]
    return {"status": "success", "message": "Academic history updated successfully"}

@app.delete("/api/v1/students/{student_id}/history")
def delete_history(student_id: str):
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    
    database[student_id]["history"] = []
    return {"status": "success"}

# --- PLAN ENDPOINTS (JSON IN) ---
@app.post("/api/v1/students/{student_id}/plan")
def create_plan(student_id: str, plan_data: PlanInput):
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    database[student_id]["plan"] = [course.model_dump() for course in plan_data.planned_courses]
    return {"status": "success", "planned_courses_saved": len(database[student_id]["plan"])}

@app.put("/api/v1/students/{student_id}/plan")
def update_plan(student_id: str, plan_data: PlanInput):
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    database[student_id]["plan"] = [course.model_dump() for course in plan_data.planned_courses]
    return {"status": "success", "planned_courses_saved": len(database[student_id]["plan"])}

@app.delete("/api/v1/students/{student_id}/plan", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(student_id: str):
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    database[student_id]["plan"] = []
    return None

# --- PROFILE ENDPOINT ---
@app.get("/api/v1/students/{student_id}/profile")
def get_profile(student_id: str):
    if student_id not in database:
        raise HTTPException(status_code=404, detail="Student not found")
    return {
        "student_id": student_id,
        "history": database[student_id]["history"],
        "plan": database[student_id]["plan"]
    }