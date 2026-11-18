from __future__ import annotations

import pytest

from backend.agent import AgentService, scope_jql
from backend.models import AgentRequest, SearchResponse, TeamInfo


def test_scope_jql_preserves_order_by() -> None:
    result = scope_jql(
        "priority = Major AND assignee is EMPTY ORDER BY updated DESC",
        "project = AIM",
    )
    assert result == (
        "(project = AIM) AND (priority = Major AND assignee is EMPTY) "
        "ORDER BY updated DESC"
    )


class FakeJira:
    def __init__(self) -> None:
        self.jql = ""

    async def search_issues(self, jql: str, page: int, page_size: int) -> SearchResponse:
        self.jql = jql
        return SearchResponse(jql=jql, total=0, page=page, page_size=page_size, issues=[])


@pytest.mark.asyncio
async def test_agent_enforces_team_scope(monkeypatch) -> None:
    service = AgentService(
        ollama_url="http://unused",
        model="test",
        max_tool_calls=3,
        summary_issue_limit=50,
        teams={
            "AIM": TeamInfo(
                key="AIM",
                display_name="AIM",
                jira_filter="project = AIM",
                aliases=["AIM"],
            )
        },
    )
    replies = iter(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "search_issues", "arguments": {"jql": "priority = Major"}}}
                ],
            },
            {"role": "assistant", "content": "No matching issues."},
        ]
    )

    async def fake_chat(messages, include_tools=True):
        return next(replies)

    monkeypatch.setattr(service, "_chat", fake_chat)
    jira = FakeJira()
    response = await service.answer(AgentRequest(message="Show major AIM issues"), jira)

    assert jira.jql == "(project = AIM) AND (priority = Major)"
    assert response.answer == "No matching issues."
