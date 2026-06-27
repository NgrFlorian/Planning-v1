# Force uvicorn reload comment
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any
import generateur_planning
import os
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/rules")
async def get_rules():
    regles_path = os.path.join(os.path.dirname(__file__), "regles.json")
    if os.path.exists(regles_path):
        try:
            with open(regles_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {}

class Absence(BaseModel):
    agent: str
    week: int
    dayStr: str
    dayIndex: int
    type: str = "ABSENT"

class GenererRequest(BaseModel):
    absences: List[Absence]
    rules: Dict[str, Any]
    target_week: int = None
    previous_grid: Dict[str, List[str]] = None
    locked_shifts: Dict[str, Dict[int, str]] = None

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    file_path = os.path.join(os.path.dirname(__file__), "dashboard_planning.html")
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/generer")
async def generer(request: GenererRequest):
    # Convert absences to dictionary
    absences_dict = {}
    for absence in request.absences:
        if absence.agent not in absences_dict:
            absences_dict[absence.agent] = {}
        absences_dict[absence.agent][absence.dayIndex] = absence.type
        
    try:
        result = generateur_planning.generer_planning(
            absences_dict, 
            request.rules, 
            target_week=request.target_week, 
            previous_grid=request.previous_grid,
            locked_shifts=request.locked_shifts
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
