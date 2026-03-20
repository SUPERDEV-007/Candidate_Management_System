import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

APP_NAME = "AimployCMS"


def _resolve_data_dir() -> Path:
    configured = os.getenv("AIMPLOY_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / APP_NAME

    return Path.cwd() / APP_NAME


def _resolve_database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL", "").strip()
    if explicit_url:
        return explicit_url

    explicit_path = os.getenv("AIMPLOY_DB_PATH", "").strip()
    if explicit_path:
        db_path = Path(explicit_path).expanduser()
    else:
        db_filename = os.getenv("AIMPLOY_DB_FILE", "candidates.db").strip() or "candidates.db"
        db_path = DATA_DIR / db_filename

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.resolve().as_posix()}"


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

SQLALCHEMY_DATABASE_URL = _resolve_database_url()
CONNECT_ARGS = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=CONNECT_ARGS)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
