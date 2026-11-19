from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from backend.models import TeamInfo


def load_teams(path: Path) -> dict[str, TeamInfo]:
    if not path.exists():
        raise RuntimeError(f"Team configuration not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid team configuration YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Team configuration must be a YAML mapping")
    definitions = raw.get("teams")
    if definitions is None or definitions == {}:
        raise RuntimeError("At least one team must be configured in teams.yaml")
    if not isinstance(definitions, dict):
        raise ValueError("The 'teams' value must be a YAML mapping")

    teams: dict[str, TeamInfo] = {}
    allowed_fields = {"display_name", "jira_filter", "aliases", "verified"}
    for key, value in definitions.items():
        normalized_key = str(key).upper()
        if normalized_key in teams:
            raise ValueError(f"Duplicate team key after normalization: {normalized_key}")
        if not isinstance(value, dict):
            raise ValueError(f"Team '{key}' must be a YAML mapping")
        unknown_fields = sorted(set(value) - allowed_fields)
        if unknown_fields:
            raise ValueError(f"Unknown field in team '{key}': {', '.join(unknown_fields)}")
        try:
            team = TeamInfo(key=normalized_key, **value)
        except ValidationError as exc:
            raise ValueError(f"Invalid configuration for team '{key}': {exc}") from exc
        teams[team.key] = team
    return teams
