# Candidate Management System

Candidate Management System is a FastAPI + SQLite project for recruiter workflows: resume parsing, candidate CRUD, role-fit scoring, profile ranking, and side-by-side candidate comparison.  
It includes:
- a web API with a simple UI
- a standalone desktop mode (no local server needed)
- optional offline LLM enhancements

## Features

- Candidate CRUD: create, list, retrieve, and delete candidates
- Resume upload + auto profile extraction from PDF
- Skill normalization (legacy CSV skills are backfilled into normalized tables)
- Candidate scoring and status evaluation
- AI-assisted ranking with confidence metadata
- Candidate comparison endpoint for shortlisting decisions
- Summary generation for recruiter handoff
- Desktop app for offline/local usage

## Tech Stack

- Python
- FastAPI
- SQLAlchemy
- SQLite
- PyPDF2
- Optional local LLM (`transformers` + `torch`, local-files-only loading)

## Project Structure

```text
candidate_management_system/
  main.py                 # FastAPI app + routes
  desktop_app.py          # Tkinter desktop app
  auth.py                 # Optional API key auth
  database.py             # DB config and engine
  models.py               # SQLAlchemy models
  schemas.py              # Pydantic schemas
  crud.py                 # Data-layer logic
  utils.py                # Parsing, scoring, AI helpers
  static/                 # Web UI assets
  assets/                 # App icons
  test_api.py             # API tests
  requirements.txt
```

## Quick Start (Web API + UI)

```powershell
cd candidate_management_system
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open in browser:
- UI: `http://127.0.0.1:8000/`
- Swagger docs: `http://127.0.0.1:8000/docs`

## Desktop Mode (No Local Server)

Run directly:

```powershell
python desktop_app.py
```

Desktop mode runs locally and uses the same core business logic as the API.

## Build Windows Executable

Use the included script:

```powershell
build_desktop_exe.bat
```

Or run PyInstaller manually:

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name AimployCMS --icon assets\aimploy_icon.ico --add-data "assets\aimploy_icon.ico;assets" --add-data "assets\aimploy_icon.png;assets" desktop_app.py
```

Output:
- `dist\AimployCMS\AimployCMS.exe` (one-folder build)

### Sharing the App with Someone Else

For this build type, share:
- the entire `dist\AimployCMS\` folder (zip this folder)

Do not share only `AimployCMS.exe` from inside that folder, because it depends on bundled files in `_internal`.

## API Endpoints

Public/read:
- `GET /candidates/`
- `GET /candidates/{candidate_id}`
- `GET /candidates/compare?candidate_a_id=1&candidate_b_id=2`

Write:
- `POST /candidates/`
- `POST /candidates/auto_from_resume`
- `POST /candidates/{candidate_id}/upload_resume`
- `POST /candidates/{candidate_id}/generate_summary`
- `POST /candidates/{candidate_id}/re_evaluate`
- `POST /candidates/re_evaluate_all`
- `POST /candidates/reprocess_resumes`

Delete:
- `DELETE /candidates/{candidate_id}`

## Authentication (Optional)

Auth is disabled by default for easy demo/testing.

Enable API key auth:

```powershell
set ENABLE_API_KEY_AUTH=true
set ADMIN_API_KEYS=your-admin-key
set RECRUITER_API_KEYS=your-recruiter-key
```

Then pass `X-API-Key` in requests.

## Data and File Paths

Database path resolution (in order):
1. `DATABASE_URL` (full override)
2. `AIMPLOY_DB_PATH`
3. `AIMPLOY_DATA_DIR` + `AIMPLOY_DB_FILE` (default file: `candidates.db`)
4. Fallback: `%LOCALAPPDATA%\AimployCMS\candidates.db` on Windows

Upload directory can be overridden with:
- `UPLOAD_DIR`

## Optional Offline LLM

If model files for `google/flan-t5-small` are already available locally, the app can use them for improved extraction/ranking/summaries.

Install optional dependencies:

```powershell
pip install transformers torch
```

The app loads the model with `local_files_only=True`, so it does not require network access at runtime for model loading.

## Run Tests

```powershell
python -m pytest -q
```

## Notes

- This repository focuses on practical recruiter workflows and local-first execution.
- One-folder desktop builds (`onedir`) generally start faster than one-file builds (`onefile`).
