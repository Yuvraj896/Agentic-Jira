from __future__ import annotations

from pathlib import Path

import yaml

from backend.models import TeamInfo


def load_teams(path: Path) -> dict[str, TeamInfo]:
    if not path.exists():
        raise RuntimeError(f"Team configuration not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    teams: dict[str, TeamInfo] = {}
    for key, value in (raw.get("teams") or {}).items():
        team = TeamInfo(key=str(key).upper(), **(value or {}))
        teams[team.key] = team
    if not teams:
        raise RuntimeError("At least one team must be configured in teams.yaml")
    return teams
