from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from backend.config import Settings
from backend.models import JiraUser, SearchResponse
from backend.server import create_app


class FakeJiraClient:
    def __init__(self, pat: str) -> None:
        self.pat = pat

    async def get_current_user(self) -> JiraUser:
        assert self.pat == "valid-token"
        return JiraUser(username="yuvraj", display_name="Yuvraj")

    async def search_issues(self, jql: str, page: int, page_size: int) -> SearchResponse:
        return SearchResponse(jql=jql, total=0, page=page, page_size=page_size, issues=[])


def settings() -> Settings:
    root = Path(__file__).resolve().parent.parent
    return Settings(
        jira_base_url="https://jira.example.test",
        jira_verify_ssl=True,
        jira_timeout_seconds=2,
        ollama_url="http://127.0.0.1:11434",
        ollama_model="qwen3.5:4b",
        teams_config=root / "teams.yaml",
        session_ttl_seconds=3600,
        agent_max_tool_calls=3,
        agent_summary_issue_limit=50,
        cookie_secure=False,
        cors_origins=["http://localhost:5173"],
    )


@pytest.mark.asyncio
async def test_connect_cookie_allows_read_only_search_and_disconnect() -> None:
    app = create_app(settings())
    app.state.jira_client_factory = FakeJiraClient

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        connected = await client.post("/api/session/connect", json={"pat": "valid-token"})
        assert connected.status_code == 200
        assert connected.json()["user"]["display_name"] == "Yuvraj"
        assert "jira_live_session" in connected.cookies

        result = await client.post(
            "/api/jira/search",
            json={"jql": "project = AIM", "page": 1, "page_size": 25},
        )
        assert result.status_code == 200
        assert result.json()["total"] == 0

        disconnected = await client.delete("/api/session")
        assert disconnected.status_code == 204
        assert (await client.get("/api/session")).status_code == 401


@pytest.mark.asyncio
async def test_search_requires_session() -> None:
    app = create_app(settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/jira/search", json={"jql": "project = AIM"})
    assert response.status_code == 401
