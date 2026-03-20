import os
import re
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import crud
import database
import models
import schemas
import utils

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


def _resolve_runtime_dir(env_name: str, default_dir_name: str) -> str:
    raw = os.getenv(env_name, "").strip()
    if raw:
        return raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw)
    return os.path.join(BASE_DIR, default_dir_name)


UPLOAD_DIR = _resolve_runtime_dir("UPLOAD_DIR", "uploads")
PLACEHOLDER_EMAIL_DOMAIN = "autofill.example.com"

# Create database tables
models.Base.metadata.create_all(bind=database.engine)

@asynccontextmanager
async def lifespan(_: FastAPI):
    db = database.SessionLocal()
    try:
        result = crud.bootstrap_normalized_skills(db)
        if result["migrated_candidates"] or result["normalized_candidates"]:
            print(f"[DB] Skill bootstrap complete: {result}")
    finally:
        db.close()
    yield


app = FastAPI(
    title="Candidate Management System",
    description="API to manage candidate profiles, parse resumes, and compare applicants.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _normalize_filename(filename: str) -> str:
    base = os.path.basename(filename or "resume.pdf")
    normalized = re.sub(r"[^A-Za-z0-9._\-]", "_", base)
    return normalized or "resume.pdf"


def _is_placeholder_email(email: str) -> bool:
    email_lower = email.lower()
    return (
        email_lower.endswith(f"@{PLACEHOLDER_EMAIL_DOMAIN}")
        or email_lower.startswith("unknown")
        or email_lower.startswith("temp")
    )


def _build_placeholder_email(name_hint: str, db: Session) -> str:
    local_base = re.sub(r"[^a-z0-9]+", ".", (name_hint or "candidate").lower()).strip(".")
    if not local_base:
        local_base = "candidate"

    index = 1
    while True:
        email = f"{local_base}.{index}@{PLACEHOLDER_EMAIL_DOMAIN}"
        if crud.get_candidate_by_email(db, email=email) is None:
            return email
        index += 1


async def _save_resume_file(file: UploadFile, prefix: str) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_name = _normalize_filename(file.filename)
    file_path = os.path.join(UPLOAD_DIR, f"{prefix}_{file_name}")

    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())
    return file_path


def _build_strengths(candidate: models.Candidate) -> list[str]:
    evaluation = utils.evaluate_candidate(candidate.role_applied, "", candidate.skills)
    effective_score = candidate.match_score if candidate.match_score is not None else evaluation["score"]
    priority_score = utils.candidate_priority_score(candidate)

    strengths: list[str] = [f"Match score: {effective_score}%", f"AI rank score: {priority_score}"]
    if candidate.experience:
        strengths.append(f"Experience: {candidate.experience}")

    if evaluation.get("matched_skills"):
        strengths.append(f"Role-aligned skills: {evaluation['matched_skills']}")
    elif candidate.skills:
        strengths.append(f"Listed skills: {candidate.skills}")

    if evaluation.get("confidence") is not None:
        strengths.append(f"Evaluation confidence: {int(float(evaluation['confidence']) * 100)}%")
    if evaluation.get("evaluation_source"):
        strengths.append(f"Evaluation source: {evaluation['evaluation_source']}")

    strengths.append(f"Current status: {candidate.status}")
    return strengths


def _effective_match_score(candidate: models.Candidate) -> int:
    if candidate.match_score is not None:
        return candidate.match_score
    return utils.evaluate_candidate(candidate.role_applied, "", candidate.skills).get("score", 0)


@app.get("/", response_class=HTMLResponse, summary="Serve Web UI", include_in_schema=False)
def get_ui():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.post(
    "/candidates/",
    response_model=schemas.CandidateResponse,
    summary="Create a new candidate profile",
)
def create_candidate(candidate: schemas.CandidateCreate, db: Session = Depends(database.get_db)):
    db_candidate = crud.get_candidate_by_email(db, email=candidate.email)
    if db_candidate:
        raise HTTPException(status_code=400, detail="Email already registered")
    return crud.create_candidate(db=db, candidate=candidate)


@app.post(
    "/candidates/auto_from_resume",
    response_model=schemas.CandidateResponse,
    summary="Auto-create candidate profile from resume",
)
async def create_candidate_from_resume(
    role_applied: str = Form("General"),
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    file_path = await _save_resume_file(file, prefix=f"auto_{uuid4().hex[:8]}")

    text = utils.extract_text_from_pdf(file_path)
    extracted_data = utils.parse_candidate_details(text)

    candidate_name = extracted_data.get("name") or "Unknown Candidate"
    if candidate_name.lower() == "unknown":
        candidate_name = "Unknown Candidate"

    parsed_email = extracted_data.get("email")
    if parsed_email and crud.get_candidate_by_email(db, parsed_email) is None:
        candidate_email = parsed_email
    else:
        candidate_email = _build_placeholder_email(candidate_name, db)

    candidate_payload = schemas.CandidateCreate(
        name=candidate_name,
        email=candidate_email,
        role_applied=role_applied,
        skills=extracted_data.get("skills") or None,
    )
    created_candidate = crud.create_candidate(db=db, candidate=candidate_payload)

    eval_result = utils.evaluate_candidate(role_applied, text, extracted_data.get("skills"))
    summary = utils.generate_candidate_summary(
        name=created_candidate.name,
        role=role_applied,
        skills=extracted_data.get("skills") or created_candidate.skills,
        experience=extracted_data.get("experience"),
        status=eval_result["status"],
        match_score=eval_result["score"],
    )

    update_data = schemas.CandidateUpdate(
        resume_filename=file_path,
        skills=extracted_data.get("skills"),
        skills_update_mode="replace",
        status=eval_result["status"],
        experience=extracted_data.get("experience"),
        match_score=eval_result["score"],
        ai_summary=summary,
    )
    return crud.update_candidate(db=db, candidate_id=created_candidate.id, updates=update_data)


@app.get("/candidates/", response_model=list[schemas.CandidateResponse], summary="List all candidates")
def read_candidates(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    if skip < 0 or limit < 0:
        raise HTTPException(status_code=400, detail="skip and limit must be non-negative")
    candidates = crud.get_candidates(db, skip=0, limit=None)
    ranked = utils.sort_candidates_for_display(candidates)
    return ranked[skip : skip + limit]


@app.get("/candidates/{candidate_id:int}", response_model=schemas.CandidateResponse, summary="Get a specific candidate by ID")
def read_candidate(candidate_id: int, db: Session = Depends(database.get_db)):
    db_candidate = crud.get_candidate(db, candidate_id=candidate_id)
    if db_candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return db_candidate


@app.post(
    "/candidates/{candidate_id:int}/upload_resume",
    response_model=schemas.CandidateResponse,
    summary="Upload a candidate resume",
)
async def upload_resume(candidate_id: int, file: UploadFile = File(...), db: Session = Depends(database.get_db)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    db_candidate = crud.get_candidate(db, candidate_id=candidate_id)
    if db_candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    file_path = await _save_resume_file(file, prefix=f"{candidate_id}_{uuid4().hex[:8]}")

    text = utils.extract_text_from_pdf(file_path)
    extracted_data = utils.parse_candidate_details(text)
    eval_result = utils.evaluate_candidate(db_candidate.role_applied, text, extracted_data.get("skills"))

    update_values = {
        "resume_filename": file_path,
        "skills": extracted_data.get("skills"),
        "skills_update_mode": "replace",
        "status": eval_result["status"],
        "experience": extracted_data.get("experience"),
        "match_score": eval_result["score"],
    }

    parsed_name = extracted_data.get("name")
    if parsed_name and parsed_name.lower() != "unknown":
        if not db_candidate.name or db_candidate.name.strip().lower() in {"unknown", "unknown candidate", "n/a"}:
            update_values["name"] = parsed_name

    parsed_email = extracted_data.get("email")
    if parsed_email and parsed_email != db_candidate.email and _is_placeholder_email(db_candidate.email):
        existing = crud.get_candidate_by_email(db, email=parsed_email)
        if existing is None or existing.id == db_candidate.id:
            update_values["email"] = parsed_email

    summary_name = update_values.get("name") or db_candidate.name
    summary_match_score = (
        update_values["match_score"] if update_values.get("match_score") is not None else db_candidate.match_score
    )
    summary = utils.generate_candidate_summary(
        name=summary_name,
        role=db_candidate.role_applied,
        skills=update_values.get("skills") or db_candidate.skills,
        experience=update_values.get("experience") or db_candidate.experience,
        status=update_values.get("status") or db_candidate.status,
        match_score=summary_match_score,
    )
    update_values["ai_summary"] = summary

    update_data = schemas.CandidateUpdate(**update_values)
    updated_candidate = crud.update_candidate(db=db, candidate_id=candidate_id, updates=update_data)
    return updated_candidate


@app.post(
    "/candidates/{candidate_id:int}/generate_summary",
    response_model=schemas.CandidateResponse,
    summary="Generate AI summary",
)
def generate_summary(candidate_id: int, db: Session = Depends(database.get_db)):
    db_candidate = crud.get_candidate(db, candidate_id=candidate_id)
    if db_candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    summary = utils.generate_candidate_summary(
        name=db_candidate.name,
        role=db_candidate.role_applied,
        skills=db_candidate.skills,
        experience=db_candidate.experience,
        status=db_candidate.status,
        match_score=db_candidate.match_score,
    )

    update_data = schemas.CandidateUpdate(ai_summary=summary)
    return crud.update_candidate(db=db, candidate_id=candidate_id, updates=update_data)


@app.post(
    "/candidates/{candidate_id:int}/re_evaluate",
    response_model=schemas.CandidateResponse,
    summary="Re-evaluate candidate using current AI ranking",
)
def re_evaluate_candidate(candidate_id: int, db: Session = Depends(database.get_db)):
    db_candidate = crud.get_candidate(db, candidate_id=candidate_id)
    if db_candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    eval_result = utils.evaluate_candidate(db_candidate.role_applied, "", db_candidate.skills)
    summary = utils.generate_candidate_summary(
        name=db_candidate.name,
        role=db_candidate.role_applied,
        skills=db_candidate.skills,
        experience=db_candidate.experience,
        status=eval_result["status"],
        match_score=eval_result["score"],
    )
    update_data = schemas.CandidateUpdate(
        status=eval_result["status"],
        match_score=eval_result["score"],
        ai_summary=summary,
    )
    return crud.update_candidate(db=db, candidate_id=candidate_id, updates=update_data)


@app.post(
    "/candidates/re_evaluate_all",
    summary="Re-evaluate all candidates using current AI ranking",
)
def re_evaluate_all_candidates(db: Session = Depends(database.get_db)):
    candidates = crud.get_candidates(db, skip=0, limit=None)
    updated_ids: list[int] = []
    for candidate in candidates:
        eval_result = utils.evaluate_candidate(candidate.role_applied, "", candidate.skills)
        summary = utils.generate_candidate_summary(
            name=candidate.name,
            role=candidate.role_applied,
            skills=candidate.skills,
            experience=candidate.experience,
            status=eval_result["status"],
            match_score=eval_result["score"],
        )
        update_data = schemas.CandidateUpdate(
            status=eval_result["status"],
            match_score=eval_result["score"],
            ai_summary=summary,
        )
        crud.update_candidate(db=db, candidate_id=candidate.id, updates=update_data)
        updated_ids.append(candidate.id)
    return {"updated_count": len(updated_ids), "updated_ids": updated_ids}


@app.post(
    "/candidates/reprocess_resumes",
    summary="Re-parse all stored resumes and refresh candidate fields",
)
def reprocess_all_resumes(db: Session = Depends(database.get_db)):
    candidates = crud.get_candidates(db, skip=0, limit=None)
    updated_ids: list[int] = []
    skipped_ids: list[int] = []

    for candidate in candidates:
        if not candidate.resume_filename:
            skipped_ids.append(candidate.id)
            continue

        resume_path = candidate.resume_filename
        if not os.path.isabs(resume_path):
            resume_path = os.path.join(BASE_DIR, resume_path)

        if not os.path.exists(resume_path):
            skipped_ids.append(candidate.id)
            continue

        text = utils.extract_text_from_pdf(resume_path)
        extracted_data = utils.parse_candidate_details(text)
        eval_result = utils.evaluate_candidate(candidate.role_applied, text, extracted_data.get("skills") or candidate.skills)

        summary = utils.generate_candidate_summary(
            name=candidate.name,
            role=candidate.role_applied,
            skills=extracted_data.get("skills") or candidate.skills,
            experience=extracted_data.get("experience") or candidate.experience,
            status=eval_result["status"],
            match_score=eval_result["score"],
        )

        update_data = schemas.CandidateUpdate(
            skills=extracted_data.get("skills") or candidate.skills,
            skills_update_mode="replace",
            experience=extracted_data.get("experience") or candidate.experience,
            status=eval_result["status"],
            match_score=eval_result["score"],
            ai_summary=summary,
        )
        crud.update_candidate(db=db, candidate_id=candidate.id, updates=update_data)
        updated_ids.append(candidate.id)

    return {
        "updated_count": len(updated_ids),
        "updated_ids": updated_ids,
        "skipped_count": len(skipped_ids),
        "skipped_ids": skipped_ids,
    }


@app.get(
    "/candidates/compare",
    response_model=schemas.CandidateComparisonResponse,
    summary="Compare two candidates side by side",
)
def compare_candidates(candidate_a_id: int, candidate_b_id: int, db: Session = Depends(database.get_db)):
    if candidate_a_id == candidate_b_id:
        raise HTTPException(status_code=400, detail="Please provide two different candidate IDs")

    candidate_a = crud.get_candidate(db, candidate_id=candidate_a_id)
    candidate_b = crud.get_candidate(db, candidate_id=candidate_b_id)

    if candidate_a is None or candidate_b is None:
        raise HTTPException(status_code=404, detail="One or both candidates were not found")

    same_role = candidate_a.role_applied.strip().lower() == candidate_b.role_applied.strip().lower()
    role_context = candidate_a.role_applied if same_role else f"{candidate_a.role_applied} vs {candidate_b.role_applied}"

    score_a = _effective_match_score(candidate_a)
    score_b = _effective_match_score(candidate_b)
    priority_a = utils.candidate_priority_score(candidate_a)
    priority_b = utils.candidate_priority_score(candidate_b)

    years_a = utils.extract_experience_years(candidate_a.experience)
    years_b = utils.extract_experience_years(candidate_b.experience)

    if priority_a > priority_b + 2:
        better_fit = "candidate_a"
        recommendation = f"{candidate_a.name} is ranked higher by the AI sorting score for this role context."
    elif priority_b > priority_a + 2:
        better_fit = "candidate_b"
        recommendation = f"{candidate_b.name} is ranked higher by the AI sorting score for this role context."
    elif score_a > score_b + 5:
        better_fit = "candidate_a"
        recommendation = f"AI rank is close, but {candidate_a.name} has the stronger role-match score."
    elif score_b > score_a + 5:
        better_fit = "candidate_b"
        recommendation = f"AI rank is close, but {candidate_b.name} has the stronger role-match score."
    elif years_a > years_b + 0.5:
        better_fit = "candidate_a"
        recommendation = f"AI rank and match scores are close, but {candidate_a.name} shows stronger experience depth."
    elif years_b > years_a + 0.5:
        better_fit = "candidate_b"
        recommendation = f"AI rank and match scores are close, but {candidate_b.name} shows stronger experience depth."
    else:
        better_fit = "tie"
        recommendation = "Both candidates appear similarly competitive for this role context."

    return schemas.CandidateComparisonResponse(
        same_role=same_role,
        role_context=role_context,
        candidate_a=schemas.CandidateComparisonItem(
            id=candidate_a.id,
            name=candidate_a.name,
            email=candidate_a.email,
            role_applied=candidate_a.role_applied,
            status=candidate_a.status,
            experience=candidate_a.experience,
            match_score=score_a,
            skills=candidate_a.skills,
            ai_summary=candidate_a.ai_summary,
        ),
        candidate_b=schemas.CandidateComparisonItem(
            id=candidate_b.id,
            name=candidate_b.name,
            email=candidate_b.email,
            role_applied=candidate_b.role_applied,
            status=candidate_b.status,
            experience=candidate_b.experience,
            match_score=score_b,
            skills=candidate_b.skills,
            ai_summary=candidate_b.ai_summary,
        ),
        candidate_a_strengths=_build_strengths(candidate_a),
        candidate_b_strengths=_build_strengths(candidate_b),
        better_fit=better_fit,
        recommendation=recommendation,
    )


@app.delete(
    "/candidates/{candidate_id:int}",
    summary="Delete a candidate profile",
)
def delete_candidate(candidate_id: int, db: Session = Depends(database.get_db)):
    db_candidate = crud.get_candidate(db, candidate_id=candidate_id)
    if not db_candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    db.delete(db_candidate)
    db.commit()
    return {"detail": "Candidate deleted successfully"}
