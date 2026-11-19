from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from backend.jira_client import JiraClient, JiraError
from backend.models import (
    AgentRequest,
    AgentResponse,
    ChartPoint,
    IssueSummary,
    JiraUser,
    SearchResponse,
    TeamInfo,
    Visualization,
)


class OpenAIError(RuntimeError):
    """A safe, user-facing OpenAI integration error."""


logger = logging.getLogger("uvicorn.error")
ORDER_BY_RE = re.compile(r"(?:^|\s+)ORDER\s+BY\s+", re.IGNORECASE)


@dataclass(frozen=True)
class SearchPlan:
    jql: str
    person_query: str
    response_mode: Literal["list", "summary", "visualization"]
    chart_type: Literal["none", "bar", "pie"]
    group_by: Literal["none", "status", "priority", "assignee", "issue_type"]


def scope_jql(jql: str, team_filter: str) -> str:
    """Enforce the active team filter while preserving an optional ORDER BY."""
    jql = jql.strip()
    match = ORDER_BY_RE.search(jql)
    if match:
        predicate = jql[: match.start()].strip()
        order_by = jql[match.start() :].strip()
    else:
        predicate = jql
        order_by = ""
    if not predicate:
        predicate = "issuekey is not EMPTY"
    scoped = f"({team_filter}) AND ({predicate})"
    return f"{scoped} {order_by}".strip()


def quote_jql(value: str) -> str:
    return f'"{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'


def add_jql_predicate(jql: str, predicate: str) -> str:
    match = ORDER_BY_RE.search(jql)
    if match:
        filters = jql[: match.start()].strip()
        order_by = jql[match.start() :].strip()
    else:
        filters = jql.strip()
        order_by = ""
    combined = f"({filters}) AND ({predicate})" if filters else predicate
    return f"{combined} {order_by}".strip()


PLAN_TOOL = {
    "type": "function",
    "name": "plan_jira_search",
    "description": "Create one safe read-only Jira search and presentation plan.",
    "parameters": {
        "type": "object",
        "properties": {
            "jql": {
                "type": "string",
                "description": "JQL predicates excluding project and excluding assignee when person_query is set.",
            },
            "person_query": {
                "type": "string",
                "description": "Person text from the request, such as yuvraj. Empty when no person is requested.",
            },
            "response_mode": {
                "type": "string",
                "enum": ["list", "summary", "visualization"],
            },
            "chart_type": {"type": "string", "enum": ["none", "bar", "pie"]},
            "group_by": {
                "type": "string",
                "enum": ["none", "status", "priority", "assignee", "issue_type"],
            },
        },
        "required": ["jql", "person_query", "response_mode", "chart_type", "group_by"],
        "additionalProperties": False,
    },
    "strict": True,
}

ANSWER_TOOL = {
    "type": "function",
    "name": "present_grounded_answer",
    "description": "Return a concise answer grounded only in the supplied Jira evidence and metrics.",
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Concise Markdown. Cite relevant Jira issue keys and do not invent facts.",
            }
        },
        "required": ["answer"],
        "additionalProperties": False,
    },
    "strict": True,
}


class AgentService:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        teams: dict[str, TeamInfo],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.teams = teams
        self.transport = transport

    async def answer(self, request: AgentRequest, jira: JiraClient) -> AgentResponse:
        team = self.teams.get(request.team_key.upper())
        if team is None:
            raise JiraError(f"Unknown team: {request.team_key}", 400)

        plan = await self._create_plan(request, team)
        proposed_jql = plan.jql
        if plan.person_query:
            resolution = await self._resolve_person(jira, plan.person_query, team.key)
            if isinstance(resolution, AgentResponse):
                return resolution
            person_clause = f"assignee = {quote_jql(resolution.display_name)}"
            proposed_jql = add_jql_predicate(proposed_jql, person_clause)

        jql = scope_jql(proposed_jql, team.jira_filter)
        logger.info("[AIM] Agent selected Jira search: jql=%s", jql)
        search = await self._search_issues(jira, jql)
        logger.info("[AIM] Jira evidence ready: total=%s fetched=%s", search.total, len(search.issues))

        warnings: list[str] = []
        if search.total > len(search.issues):
            warnings.append(
                f"Jira reported {search.total} matches but returned {len(search.issues)} issues. The analysis may be incomplete."
            )
        if not search.issues:
            return self._response("No AIM issues matched this request.", search, warnings)

        visualization = self._build_visualization(plan, search.issues)
        if plan.response_mode == "list":
            answer = f"Found {search.total} AIM issue{'s' if search.total != 1 else ''}. The live Jira results are shown below."
        else:
            detail: dict[str, Any] | None = None
            if len(search.issues) == 1:
                issue = await jira.get_issue(search.issues[0].key)
                detail = {
                    "key": issue.key,
                    "summary": issue.summary,
                    "description": issue.description,
                    "status": issue.status,
                    "priority": issue.priority,
                    "assignee": issue.assignee,
                    "components": issue.components,
                    "labels": issue.labels,
                    "updated": issue.updated,
                }
            answer = await self._summarize(request.message, search, detail)

        return self._response(answer, search, warnings, visualization)

    async def _resolve_person(
        self,
        jira: JiraClient,
        query: str,
        project: str,
    ) -> JiraUser | AgentResponse:
        candidates = await jira.find_assignable_users(query, project)
        if not candidates:
            return AgentResponse(
                answer=f"I could not find an AIM-assignable Jira user matching “{query}”. Try their full Jira display name.",
                warnings=["No Jira user was selected, so no issue search was run."],
            )
        normalized = query.casefold().strip()
        exact = [
            user
            for user in candidates
            if normalized in {user.username.casefold(), user.display_name.casefold()}
        ]
        if len(exact) == 1:
            return exact[0]
        if len(candidates) == 1:
            return candidates[0]
        names = ", ".join(user.display_name for user in candidates[:5])
        return AgentResponse(
            answer=f"I found multiple Jira users matching “{query}”: {names}. Please ask again using the full display name.",
            warnings=["No Jira user was selected, so no issue search was run."],
        )

    async def _search_issues(self, jira: JiraClient, jql: str) -> SearchResponse:
        page_size = 100
        first = await jira.search_issues(jql=jql, page=1, page_size=page_size)
        issues = list(first.issues)
        page_count = (first.total + page_size - 1) // page_size
        for page in range(2, page_count + 1):
            next_page = await jira.search_issues(jql=jql, page=page, page_size=page_size)
            if not next_page.issues:
                break
            issues.extend(next_page.issues)
        return SearchResponse(
            jql=jql,
            total=first.total,
            page=1,
            page_size=page_size,
            issues=issues,
        )

    def _response(
        self,
        answer: str,
        search: SearchResponse,
        warnings: list[str],
        visualization: Visualization | None = None,
    ) -> AgentResponse:
        return AgentResponse(
            answer=answer,
            jql=search.jql,
            total=search.total,
            issues=search.issues,
            warnings=warnings,
            visualization=visualization,
        )

    async def _create_plan(self, request: AgentRequest, team: TeamInfo) -> SearchPlan:
        history = [
            {"role": item.role, "content": item.content}
            for item in request.history[-6:]
        ]
        arguments = await self._call_tool(
            instructions=self._planning_prompt(team),
            input_items=[*history, {"role": "user", "content": request.message}],
            tool=PLAN_TOOL,
        )
        return SearchPlan(
            jql=str(arguments.get("jql") or ""),
            person_query=str(arguments.get("person_query") or "").strip(),
            response_mode=arguments["response_mode"],
            chart_type=arguments["chart_type"],
            group_by=arguments["group_by"],
        )

    async def _summarize(
        self,
        question: str,
        search: SearchResponse,
        detail: dict[str, Any] | None,
    ) -> str:
        metrics = self._metrics(search.issues)
        evidence = [
            {
                "key": issue.key,
                "summary": issue.summary,
                "status": issue.status,
                "priority": issue.priority,
                "assignee": issue.assignee,
                "issue_type": issue.issue_type,
                "created": issue.created,
                "updated": issue.updated,
            }
            for issue in search.issues
        ]
        arguments = await self._call_tool(
            instructions=(
                "Answer using only the supplied Jira evidence and deterministic metrics. "
                "Never invent blockers, causes, completion, dates, or counts. Cite issue keys for issue-specific claims. "
                "If the data is insufficient, say what is missing. Keep the answer concise."
            ),
            input_items=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": question,
                            "jql": search.jql,
                            "matched_total": search.total,
                            "provided_issue_count": len(evidence),
                            "metrics": metrics,
                            "issues": evidence,
                            "single_issue_detail": detail,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            tool=ANSWER_TOOL,
        )
        return str(arguments.get("answer") or "I could not summarize the Jira evidence.")

    async def _call_tool(
        self,
        instructions: str,
        input_items: list[dict[str, str]],
        tool: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.api_key:
            raise OpenAIError(
                "OpenAI is not configured. Add OPENAI_API_KEY to .env, then restart make dev. Manual JQL still works."
            )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": input_items,
            "tools": [tool],
            "tool_choice": {"type": "function", "name": tool["name"]},
            "parallel_tool_calls": False,
            "store": False,
        }
        body = await self._post_openai(payload)
        for item in body.get("output", []):
            if item.get("type") != "function_call" or item.get("name") != tool["name"]:
                continue
            arguments = item.get("arguments", "{}")
            try:
                return json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError as exc:
                raise OpenAIError("OpenAI returned invalid tool arguments.") from exc
        raise OpenAIError("OpenAI did not return the expected Jira tool call. Try rephrasing the question.")

    async def _post_openai(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        logger.info("[AIM] OpenAI request started: model=%s", self.model)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url}/responses",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.warning("[AIM] OpenAI request failed after %.1fs", time.monotonic() - started)
            raise OpenAIError(
                "OpenAI could not be reached. Check your internet connection. Manual JQL still works."
            ) from exc
        logger.info(
            "[AIM] OpenAI response received: status=%s elapsed=%.1fs",
            response.status_code,
            time.monotonic() - started,
        )
        if response.status_code == 401:
            raise OpenAIError("OpenAI rejected the API key. Check OPENAI_API_KEY in .env and restart make dev.")
        if response.status_code == 429:
            raise OpenAIError("OpenAI rate limit or API quota was reached. Check the API project's billing and limits.")
        if response.is_error:
            raise OpenAIError(
                f"OpenAI returned an error ({response.status_code}). Check that model '{self.model}' is available to this API project."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise OpenAIError("OpenAI returned an invalid response.") from exc

    @staticmethod
    def _metrics(issues: list[IssueSummary]) -> dict[str, Any]:
        def counts(field: str) -> dict[str, int]:
            return dict(Counter(str(getattr(issue, field) or "Unknown") for issue in issues).most_common())

        return {
            "total_provided": len(issues),
            "by_status": counts("status"),
            "by_priority": counts("priority"),
            "by_assignee": counts("assignee"),
            "by_issue_type": counts("issue_type"),
        }

    def _build_visualization(
        self,
        plan: SearchPlan,
        issues: list[IssueSummary],
    ) -> Visualization | None:
        if plan.response_mode != "visualization" or not issues:
            return None
        group_by = plan.group_by if plan.group_by != "none" else "status"
        chart_type = plan.chart_type if plan.chart_type != "none" else "bar"
        labels = {
            "status": "status",
            "priority": "priority",
            "assignee": "assignee",
            "issue_type": "issue type",
        }
        counts = Counter(str(getattr(issue, group_by) or "Unknown") for issue in issues)
        return Visualization(
            type=chart_type,
            title=f"AIM issues by {labels[group_by]}",
            data=[ChartPoint(label=label, value=value) for label, value in counts.most_common()],
        )

    @staticmethod
    def _planning_prompt(team: TeamInfo) -> str:
        aliases = ", ".join(team.aliases)
        return f"""Create one read-only Jira Data Center search plan for {team.display_name} ({aliases}).
The backend always enforces {team.jira_filter}; never put a project predicate in jql.
If a person is named, copy that text into person_query and do not put an assignee predicate in jql.
Use an empty person_query when no person is requested.
Use response_mode=list for show/list/give requests, summary for summarize/analyze, and visualization for chart/visualize/group/distribution requests.
For visualization choose bar or pie and group by status, priority, assignee, or issue_type.
For the current/active sprint use `sprint in openSprints()`.
For unassigned issues use `assignee is EMPTY` and leave person_query empty.
Include `ORDER BY updated DESC` when useful.
Never create, update, transition, comment on, or delete Jira data.
"""
