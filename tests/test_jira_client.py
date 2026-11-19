from __future__ import annotations

import httpx
import pytest

from backend.jira_client import JiraClient


def make_client(handler) -> JiraClient:
    return JiraClient(
        base_url="https://jira.example.test",
        pat="secret-pat",
        verify_ssl=True,
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_search_normalizes_jira_results_and_paginates() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-pat"
        payload = __import__("json").loads(request.content)
        assert payload["startAt"] == 25
        assert payload["maxResults"] == 25
        return httpx.Response(
            200,
            json={
                "total": 31,
                "issues": [
                    {
                        "key": "AIM-42",
                        "fields": {
                            "summary": "Packet processing failure",
                            "status": {"name": "In Progress"},
                            "priority": {"name": "Major"},
                            "assignee": None,
                            "issuetype": {"name": "Bug"},
                            "project": {"key": "AIM"},
                            "created": "2026-11-01T08:00:00.000+0000",
                            "updated": "2026-11-17T08:00:00.000+0000",
                        },
                    }
                ],
            },
        )

    result = await make_client(handler).search_issues("priority = Major", page=2, page_size=25)

    assert result.total == 31
    assert result.page == 2
    assert result.issues[0].key == "AIM-42"
    assert result.issues[0].assignee == "Unassigned"
    assert result.issues[0].url == "https://jira.example.test/browse/AIM-42"


@pytest.mark.asyncio
async def test_issue_detail_includes_comments_and_named_custom_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/comment"):
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {
                            "id": "7",
                            "author": {"displayName": "Sam"},
                            "body": "Waiting for logs",
                            "created": "2026-11-17",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "key": "AIM-7",
                "names": {"customfield_12300": "Release Train"},
                "fields": {
                    "summary": "Investigate timeout",
                    "status": {"name": "Open"},
                    "priority": {"name": "Major"},
                    "assignee": {"displayName": "Alex"},
                    "issuetype": {"name": "Bug"},
                    "project": {"key": "AIM"},
                    "description": "Observed during sanity run",
                    "labels": ["sanity"],
                    "components": [{"name": "MCP"}],
                    "customfield_12300": {"value": "R1"},
                },
            },
        )

    result = await make_client(handler).get_issue("AIM-7")

    assert result.fields["Release Train"] == "R1"
    assert result.comments[0].body == "Waiting for logs"
    assert result.components == ["MCP"]


@pytest.mark.asyncio
async def test_find_assignable_users_uses_project_scoped_jira_lookup() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/user/assignable/search")
        assert request.url.params["username"] == "yuvraj"
        assert request.url.params["project"] == "AIM"
        return httpx.Response(
            200,
            json=[
                {
                    "name": "ymandrah",
                    "displayName": "Yuvraj Mandrah",
                    "emailAddress": "yuvraj@example.test",
                }
            ],
        )

    users = await make_client(handler).find_assignable_users("yuvraj", "AIM")

    assert users[0].username == "ymandrah"
    assert users[0].display_name == "Yuvraj Mandrah"
