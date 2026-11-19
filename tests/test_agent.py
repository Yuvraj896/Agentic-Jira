from __future__ import annotations

import json

import httpx
import pytest

from backend.agent import AgentService, OpenAIError, SearchPlan, scope_jql
from backend.models import AgentRequest, IssueDetail, IssueSummary, JiraUser, SearchResponse, TeamInfo


def test_scope_jql_preserves_order_by() -> None:
    result = scope_jql(
        "priority = Major AND assignee is EMPTY ORDER BY updated DESC",
        "project = AIM",
    )
    assert result == (
        "(project = AIM) AND (priority = Major AND assignee is EMPTY) "
        "ORDER BY updated DESC"
    )


def issue(**overrides) -> IssueSummary:
    values = {
        "key": "AIM-42",
        "summary": "Packet processing failure",
        "status": "In Progress",
        "priority": "Major",
        "assignee": "Yuvraj Mandrah",
        "issue_type": "Bug",
        "project": "AIM",
        "created": "2026-11-01",
        "updated": "2026-11-17",
        "url": "https://jira.example.test/browse/AIM-42",
    }
    values.update(overrides)
    return IssueSummary(**values)


class FakeJira:
    def __init__(self, issues=None, users=None) -> None:
        self.jql = ""
        self.issues = issues or []
        self.users = users or []
        self.pages = []

    async def find_assignable_users(self, query: str, project: str):
        assert project == "AIM"
        return self.users

    async def search_issues(self, jql: str, page: int, page_size: int) -> SearchResponse:
        self.jql = jql
        self.pages.append(page)
        start = (page - 1) * page_size
        return SearchResponse(
            jql=jql,
            total=len(self.issues),
            page=page,
            page_size=page_size,
            issues=self.issues[start : start + page_size],
        )

    async def get_issue(self, issue_key: str) -> IssueDetail:
        summary = self.issues[0]
        return IssueDetail(**summary.model_dump(), description="Failure during the sanity run")


def service(**overrides) -> AgentService:
    values = {
        "api_key": "test-key",
        "base_url": "https://api.openai.test/v1",
        "model": "test-model",
        "timeout_seconds": 2,
        "teams": {
            "AIM": TeamInfo(
                key="AIM",
                display_name="AIM",
                jira_filter="project = AIM",
                aliases=["AIM"],
            )
        },
    }
    values.update(overrides)
    return AgentService(**values)


@pytest.mark.asyncio
async def test_agent_enforces_team_scope(monkeypatch) -> None:
    agent = service()

    async def fake_plan(request, team):
        return SearchPlan("priority = Major", "", "list", "none", "none")

    monkeypatch.setattr(agent, "_create_plan", fake_plan)
    jira = FakeJira()
    response = await agent.answer(AgentRequest(message="Show major AIM issues"), jira)

    assert jira.jql == "(project = AIM) AND (priority = Major)"
    assert response.answer == "No AIM issues matched this request."


@pytest.mark.asyncio
async def test_agent_fetches_every_jira_result_page() -> None:
    agent = service()
    jira = FakeJira(issues=[issue(key=f"AIM-{number}") for number in range(1, 206)])

    result = await agent._search_issues(jira, "project = AIM")

    assert len(result.issues) == 205
    assert jira.pages == [1, 2, 3]


@pytest.mark.asyncio
async def test_agent_resolves_person_through_jira_before_search(monkeypatch) -> None:
    agent = service()

    async def fake_plan(request, team):
        return SearchPlan("priority = Major ORDER BY updated DESC", "yuvraj", "list", "none", "none")

    monkeypatch.setattr(agent, "_create_plan", fake_plan)
    jira = FakeJira(
        issues=[issue()],
        users=[JiraUser(username="ymandrah", display_name="Yuvraj Mandrah")],
    )
    response = await agent.answer(AgentRequest(message="Major tasks assigned to yuvraj"), jira)

    assert jira.jql == (
        '(project = AIM) AND ((priority = Major) AND (assignee = "Yuvraj Mandrah")) '
        "ORDER BY updated DESC"
    )
    assert response.total == 1


@pytest.mark.asyncio
async def test_person_only_query_keeps_order_by_after_assignee(monkeypatch) -> None:
    agent = service()

    async def fake_plan(request, team):
        return SearchPlan("ORDER BY updated DESC", "Yuvraj Mandrah", "list", "none", "none")

    monkeypatch.setattr(agent, "_create_plan", fake_plan)
    jira = FakeJira(
        issues=[issue()],
        users=[JiraUser(username="ymandrah", display_name="Yuvraj Mandrah")],
    )

    await agent.answer(AgentRequest(message="All tasks assigned to Yuvraj Mandrah and their status"), jira)

    assert jira.jql == (
        '(project = AIM) AND (assignee = "Yuvraj Mandrah") '
        "ORDER BY updated DESC"
    )


@pytest.mark.asyncio
async def test_agent_does_not_guess_between_multiple_people(monkeypatch) -> None:
    agent = service()

    async def fake_plan(request, team):
        return SearchPlan("", "alex", "list", "none", "none")

    monkeypatch.setattr(agent, "_create_plan", fake_plan)
    jira = FakeJira(
        users=[
            JiraUser(username="alex1", display_name="Alex One"),
            JiraUser(username="alex2", display_name="Alex Two"),
        ]
    )
    response = await agent.answer(AgentRequest(message="Show Alex's issues"), jira)

    assert "multiple Jira users" in response.answer
    assert jira.jql == ""


@pytest.mark.asyncio
async def test_visualization_uses_backend_calculated_counts(monkeypatch) -> None:
    agent = service()

    async def fake_plan(request, team):
        return SearchPlan("sprint in openSprints()", "", "visualization", "bar", "status")

    async def fake_summary(question, search, detail):
        return "The sprint has three supplied issues."

    monkeypatch.setattr(agent, "_create_plan", fake_plan)
    monkeypatch.setattr(agent, "_summarize", fake_summary)
    jira = FakeJira(
        issues=[
            issue(key="AIM-1", status="Open"),
            issue(key="AIM-2", status="Open"),
            issue(key="AIM-3", status="Done"),
        ]
    )
    response = await agent.answer(AgentRequest(message="Visualize the sprint by status"), jira)

    assert response.visualization is not None
    assert response.visualization.type == "bar"
    assert response.visualization.model_dump()["data"] == [
        {"label": "Open", "value": 2},
        {"label": "Done", "value": 1},
    ]


@pytest.mark.asyncio
async def test_openai_planning_call_uses_strict_structured_tool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["tools"][0]["strict"] is True
        assert body["tools"][0]["parameters"]["additionalProperties"] is False
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "name": "plan_jira_search",
                        "arguments": json.dumps(
                            {
                                "jql": "priority = Major",
                                "person_query": "yuvraj",
                                "response_mode": "list",
                                "chart_type": "none",
                                "group_by": "none",
                            }
                        ),
                    }
                ]
            },
        )

    agent = service(transport=httpx.MockTransport(handler))
    plan = await agent._create_plan(AgentRequest(message="Show Yuvraj's major issues"), agent.teams["AIM"])

    assert plan.person_query == "yuvraj"
    assert plan.jql == "priority = Major"


@pytest.mark.asyncio
async def test_missing_openai_key_has_actionable_error() -> None:
    agent = service(api_key="")

    with pytest.raises(OpenAIError, match="OPENAI_API_KEY"):
        await agent._create_plan(AgentRequest(message="Show my issues"), agent.teams["AIM"])
