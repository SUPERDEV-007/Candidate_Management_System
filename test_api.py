import os
import shutil
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["UPLOAD_DIR"] = "test_uploads"
os.environ["ENABLE_API_KEY_AUTH"] = "false"

from database import Base, get_db
from main import app
import models
import utils

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_candidates.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_and_teardown():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if os.path.exists("test_uploads"):
        shutil.rmtree("test_uploads")


def test_create_candidate_public_mode():
    response = client.post(
        "/candidates/",
        json={"name": "John Doe", "email": "johndoe@example.com", "role_applied": "Backend Engineer (Python)"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["email"] == "johndoe@example.com"
    assert "id" in data


def test_read_candidates_public_mode():
    client.post(
        "/candidates/",
        json={"name": "Alice", "email": "alice@example.com", "role_applied": "Data Scientist"},
    )

    response = client.get("/candidates/")
    assert response.status_code == 200
    data = response.json()
    assert any(candidate["name"] == "Alice" for candidate in data)


@patch("utils.extract_text_from_pdf")
def test_upload_resume_populates_normalized_skills(mock_extract):
    mock_extract.return_value = "Jane Doe\njanedoe@example.com\nSkills: Python, FastAPI, SQL"

    create_response = client.post(
        "/candidates/",
        json={"name": "Jane", "email": "jane@example.com", "role_applied": "Backend Engineer (Python)"},
    )
    candidate_id = create_response.json()["id"]

    response = client.post(
        f"/candidates/{candidate_id}/upload_resume",
        files={"file": ("resume.pdf", b"dummy pdf content", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert "python" in (data["skills"] or "").lower()
    assert "fastapi" in (data["skills"] or "").lower()
    assert data["resume_filename"] is not None

    db = TestingSessionLocal()
    try:
        skills_count = db.query(models.Skill).count()
        links_count = db.query(models.CandidateSkill).count()
    finally:
        db.close()

    assert skills_count >= 2
    assert links_count >= 2


@patch("utils.extract_text_from_pdf")
def test_auto_create_candidate_from_resume(mock_extract):
    mock_extract.return_value = (
        "Jane Resume\n"
        "jane.resume@example.com\n"
        "Experience: 4 years\n"
        "Skills: Python, FastAPI, SQL, Docker\n"
    )

    response = client.post(
        "/candidates/auto_from_resume",
        data={"role_applied": "Backend Engineer (Python)"},
        files={"file": ("resume.pdf", b"pdf content", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] in {"Jane Resume", "Unknown Candidate", "Jane Resume".title()}
    assert data["experience"] == "4 Years"
    assert "python" in (data["skills"] or "").lower()
    assert data["resume_filename"] is not None


def test_delete_candidate_public_mode():
    created = client.post(
        "/candidates/",
        json={"name": "Del Test", "email": "del@example.com", "role_applied": "General"},
    ).json()

    delete_response = client.delete(f"/candidates/{created['id']}")
    assert delete_response.status_code == 200


def test_compare_candidates():
    first = client.post(
        "/candidates/",
        json={
            "name": "Candidate One",
            "email": "candidate.one@example.com",
            "role_applied": "Backend Engineer (Python)",
            "skills": "Python, FastAPI, SQL, Docker, AWS",
        },
    ).json()

    second = client.post(
        "/candidates/",
        json={
            "name": "Candidate Two",
            "email": "candidate.two@example.com",
            "role_applied": "Backend Engineer (Python)",
            "skills": "HTML, CSS",
        },
    ).json()

    response = client.get(f"/candidates/compare?candidate_a_id={first['id']}&candidate_b_id={second['id']}")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["candidate_a"]["id"] == first["id"]
    assert data["candidate_b"]["id"] == second["id"]
    assert data["better_fit"] in {"candidate_a", "candidate_b", "tie"}
    assert data["recommendation"]


def test_parse_experience_from_internship_date_range():
    text = (
        "Work Experience\n"
        "Software Engineering Intern\n"
        "May 2025 - Jul 2025\n"
        "Built backend APIs.\n"
    )
    details = utils.parse_candidate_details(text)
    assert details["experience"] is not None
    assert "Month" in details["experience"] or "Year" in details["experience"]


def test_education_date_range_not_treated_as_work_experience():
    text = (
        "Education\n"
        "B.Tech Computer Science\n"
        "Aug 2022 - May 2026\n"
    )
    details = utils.parse_candidate_details(text)
    assert details["experience"] is None
