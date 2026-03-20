from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    role_applied = Column(String, nullable=False, default="General")
    status = Column(String, nullable=False, default="Pending")
    experience = Column(String, nullable=True)
    match_score = Column(Integer, nullable=True)
    # Kept for API backward compatibility; normalized records live in candidate_skills.
    skills = Column(Text, nullable=True)
    resume_filename = Column(String, nullable=True)
    ai_summary = Column(Text, nullable=True)

    candidate_skills = relationship(
        "CandidateSkill",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    normalized_name = Column(String, unique=True, index=True, nullable=False)

    candidate_skills = relationship(
        "CandidateSkill",
        back_populates="skill",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CandidateSkill(Base):
    __tablename__ = "candidate_skills"

    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)
    source = Column(String, nullable=False, default="resume")

    candidate = relationship("Candidate", back_populates="candidate_skills")
    skill = relationship("Skill", back_populates="candidate_skills")
