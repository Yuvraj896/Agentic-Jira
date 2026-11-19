from __future__ import annotations

import pytest

from backend.models import TeamInfo
from backend.teams import load_teams


def test_load_teams_normalizes_keys_and_validates_definitions(tmp_path) -> None:
    config = tmp_path / "teams.yaml"
    config.write_text(
        "teams:\n"
        "  aim:\n"
        "    display_name: AIM\n"
        "    jira_filter: project = AIM\n"
        "    aliases: [AIM, AIM team]\n"
        "    verified: true\n",
        encoding="utf-8",
    )

    teams = load_teams(config)

    assert teams == {
        "AIM": TeamInfo(
            key="AIM",
            display_name="AIM",
            jira_filter="project = AIM",
            aliases=["AIM", "AIM team"],
            verified=True,
        )
    }


def test_load_teams_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Team configuration not found"):
        load_teams(tmp_path / "missing.yaml")


def test_load_teams_reports_invalid_yaml(tmp_path) -> None:
    config = tmp_path / "teams.yaml"
    config.write_text("teams: [", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid team configuration YAML"):
        load_teams(config)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("- AIM\n", "must be a YAML mapping"),
        ("teams: []\n", "must be a YAML mapping"),
        ("teams: {}\n", "At least one team must be configured"),
        ("teams:\n  AIM: null\n", "must be a YAML mapping"),
        ("teams:\n  AIM:\n    display_nam: AIM\n", "Unknown field"),
        ("teams:\n  AIM:\n    jira_filter: project = AIM\n", "Invalid configuration"),
    ],
)
def test_load_teams_rejects_invalid_structure_and_fields(tmp_path, contents: str, message: str) -> None:
    config = tmp_path / "teams.yaml"
    config.write_text(contents, encoding="utf-8")

    expected_error = RuntimeError if "At least one" in message else ValueError
    with pytest.raises(expected_error, match=message):
        load_teams(config)


def test_load_teams_rejects_keys_that_collide_after_normalization(tmp_path) -> None:
    config = tmp_path / "teams.yaml"
    config.write_text(
        "teams:\n"
        "  aim:\n"
        "    display_name: Lowercase AIM\n"
        "    jira_filter: project = AIM\n"
        "  AIM:\n"
        "    display_name: Uppercase AIM\n"
        "    jira_filter: project = AIM\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate team key after normalization: AIM"):
        load_teams(config)
