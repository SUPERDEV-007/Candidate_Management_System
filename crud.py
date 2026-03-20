import re

from sqlalchemy.orm import Session, selectinload

import models
import schemas

SKILL_DISPLAY_OVERRIDES = {
    "aws": "AWS",
    "css": "CSS",
    "gcp": "GCP",
    "html": "HTML",
    "sql": "SQL",
    "c++": "C++",
    "c#": "C#",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "fastapi": "FastAPI",
    "mongodb": "MongoDB",
    "postgresql": "PostgreSQL",
    "numpy": "NumPy",
}


def _normalize_skill_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _display_skill_name(value: str) -> str:
    normalized = _normalize_skill_name(value)
    if not normalized:
        return ""
    if normalized in SKILL_DISPLAY_OVERRIDES:
        return SKILL_DISPLAY_OVERRIDES[normalized]
    return " ".join(token.capitalize() for token in normalized.split())


def _split_skill_string(skills: str | None) -> list[str]:
    if not skills:
        return []

    tokens = re.split(r"[,;\n]", skills)
    deduped: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        display = _display_skill_name(token)
        if not display:
            continue
        normalized = _normalize_skill_name(display)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(display)

    return deduped


def _get_or_create_skill(db: Session, display_name: str) -> models.Skill:
    normalized = _normalize_skill_name(display_name)
    skill = db.query(models.Skill).filter(models.Skill.normalized_name == normalized).first()
    if skill:
        return skill

    skill = models.Skill(name=display_name, normalized_name=normalized)
    db.add(skill)
    db.flush()
    return skill


def _candidate_skill_names(db_candidate: models.Candidate) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for link in db_candidate.candidate_skills:
        if not link.skill or not link.skill.name:
            continue
        normalized = _normalize_skill_name(link.skill.name)
        if normalized in seen:
            continue
        seen.add(normalized)
        names.append(link.skill.name)

    return names


def _set_candidate_skills(db: Session, db_candidate: models.Candidate, skill_names: list[str], source: str = "resume") -> None:
    db_candidate.candidate_skills.clear()
    db.flush()

    for skill_name in skill_names:
        skill = _get_or_create_skill(db, skill_name)
        db_candidate.candidate_skills.append(models.CandidateSkill(skill=skill, source=source))

    db_candidate.skills = ", ".join(skill_names) if skill_names else None


def _sync_legacy_skill_text(db_candidate: models.Candidate) -> None:
    names = _candidate_skill_names(db_candidate)
    db_candidate.skills = ", ".join(names) if names else None


def _candidate_query_with_skills(db: Session):
    return db.query(models.Candidate).options(
        selectinload(models.Candidate.candidate_skills).selectinload(models.CandidateSkill.skill)
    )


def get_candidate(db: Session, candidate_id: int):
    candidate = _candidate_query_with_skills(db).filter(models.Candidate.id == candidate_id).first()
    if candidate:
        _sync_legacy_skill_text(candidate)
    return candidate


def get_candidate_by_email(db: Session, email: str):
    candidate = _candidate_query_with_skills(db).filter(models.Candidate.email == email).first()
    if candidate:
        _sync_legacy_skill_text(candidate)
    return candidate


def get_candidates(db: Session, skip: int = 0, limit: int | None = 100):
    query = _candidate_query_with_skills(db)
    if skip > 0:
        query = query.offset(skip)
    if limit is not None and limit >= 0:
        query = query.limit(limit)
    candidates = query.all()
    for candidate in candidates:
        _sync_legacy_skill_text(candidate)
    return candidates


def create_candidate(db: Session, candidate: schemas.CandidateCreate):
    db_candidate = models.Candidate(
        name=candidate.name,
        email=candidate.email,
        role_applied=candidate.role_applied,
        status="Pending",
    )
    db.add(db_candidate)
    db.flush()

    initial_skills = _split_skill_string(candidate.skills)
    if initial_skills:
        _set_candidate_skills(db, db_candidate, initial_skills, source="manual")

    db.commit()
    return get_candidate(db, db_candidate.id)


def update_candidate(db: Session, candidate_id: int, updates: schemas.CandidateUpdate):
    db_candidate = get_candidate(db, candidate_id)
    if not db_candidate:
        return None

    if updates.name is not None:
        db_candidate.name = updates.name
    if updates.email is not None:
        db_candidate.email = updates.email
    if updates.resume_filename is not None:
        db_candidate.resume_filename = updates.resume_filename
    if updates.status is not None:
        db_candidate.status = updates.status
    if updates.experience is not None:
        db_candidate.experience = updates.experience
    if updates.match_score is not None:
        db_candidate.match_score = updates.match_score
    if updates.ai_summary is not None:
        db_candidate.ai_summary = updates.ai_summary

    if updates.skills is not None:
        mode = (updates.skills_update_mode or "merge").strip().lower()
        if updates.skills.strip() == "":
            _set_candidate_skills(db, db_candidate, [], source="manual")
        elif mode == "replace":
            incoming = _split_skill_string(updates.skills)
            _set_candidate_skills(db, db_candidate, incoming, source="resume")
        else:
            existing = _candidate_skill_names(db_candidate)
            incoming = _split_skill_string(updates.skills)
            merged = _split_skill_string(", ".join(existing + incoming))
            _set_candidate_skills(db, db_candidate, merged, source="resume")

    db.commit()
    return get_candidate(db, candidate_id)


def bootstrap_normalized_skills(db: Session) -> dict:
    candidates = _candidate_query_with_skills(db).all()
    migrated = 0
    normalized = 0

    for candidate in candidates:
        rel_skills = _candidate_skill_names(candidate)
        csv_skills = _split_skill_string(candidate.skills)

        if not rel_skills and csv_skills:
            _set_candidate_skills(db, candidate, csv_skills, source="bootstrap")
            migrated += 1
        elif rel_skills:
            canonical = ", ".join(rel_skills)
            if candidate.skills != canonical:
                candidate.skills = canonical
                normalized += 1

    db.commit()
    return {
        "migrated_candidates": migrated,
        "normalized_candidates": normalized,
    }
