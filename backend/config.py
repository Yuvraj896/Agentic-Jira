from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    jira_base_url: str
    jira_verify_ssl: bool
    jira_timeout_seconds: float
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    openai_timeout_seconds: float
    teams_config: Path
    session_ttl_seconds: int
    cookie_secure: bool
    cors_origins: list[str]


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_dotenv()
    root = Path(__file__).resolve().parent.parent
    teams_path = Path(os.environ.get("TEAMS_CONFIG", "teams.yaml"))
    if not teams_path.is_absolute():
        teams_path = root / teams_path
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return Settings(
        jira_base_url=os.environ.get("JIRA_BASE_URL", "https://jira-prod.radcom.co.il:8443").rstrip("/"),
        jira_verify_ssl=_as_bool(os.environ.get("JIRA_VERIFY_SSL"), False),
        jira_timeout_seconds=float(os.environ.get("JIRA_TIMEOUT_SECONDS", "30")),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-6-luna"),
        openai_timeout_seconds=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "45")),
        teams_config=teams_path,
        session_ttl_seconds=int(os.environ.get("SESSION_TTL_SECONDS", str(8 * 60 * 60))),
        cookie_secure=_as_bool(os.environ.get("COOKIE_SECURE"), False),
        cors_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
    )
