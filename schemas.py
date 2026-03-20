from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional

class CandidateBase(BaseModel):
    name: str
    email: EmailStr
    role_applied: str 
    skills: Optional[str] = None
    status: Optional[str] = "Pending"
    experience: Optional[str] = None
    match_score: Optional[int] = None
    resume_filename: Optional[str] = None
    ai_summary: Optional[str] = None

class CandidateCreate(CandidateBase):
    pass

class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    resume_filename: Optional[str] = None
    skills: Optional[str] = None
    skills_update_mode: Optional[str] = None  # merge | replace
    status: Optional[str] = None
    experience: Optional[str] = None
    match_score: Optional[int] = None
    ai_summary: Optional[str] = None

class CandidateResponse(CandidateBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class CandidateComparisonItem(BaseModel):
    id: int
    name: str
    email: EmailStr
    role_applied: str
    status: str
    experience: Optional[str] = None
    match_score: Optional[int] = None
    skills: Optional[str] = None
    ai_summary: Optional[str] = None


class CandidateComparisonResponse(BaseModel):
    same_role: bool
    role_context: str
    candidate_a: CandidateComparisonItem
    candidate_b: CandidateComparisonItem
    candidate_a_strengths: list[str]
    candidate_b_strengths: list[str]
    better_fit: str
    recommendation: str
