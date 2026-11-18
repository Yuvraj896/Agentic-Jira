from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from backend.jira_client import JiraClient, JiraError
from backend.models import AgentRequest, AgentResponse, SearchResponse, TeamInfo


class OllamaError(RuntimeError):
    pass


logger = logging.getLogger("uvicorn.error")
ORDER_BY_RE = re.compile(r"\s+ORDER\s+BY\s+", re.IGNORECASE)


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


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_issues",
            "description": "Search Jira issues with read-only JQL and return current issue summaries.",
            "parameters": {
                "type": "object",
                "required": ["jql"],
                "properties": {
                    "jql": {
                        "type": "string",
                        "description": "One valid Jira Data Center JQL query. Include ORDER BY updated DESC when useful.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_issue",
            "description": "Read one Jira issue and its comments by issue key.",
            "parameters": {
                "type": "object",
                "required": ["issue_key"],
                "properties": {
                    "issue_key": {"type": "string", "description": "A Jira issue key such as AIM-123"}
                },
            },
        },
    },
]


class AgentService:
    def __init__(
        self,
        ollama_url: str,
        model: str,
        max_tool_calls: int,
        summary_issue_limit: int,
        teams: dict[str, TeamInfo],
    ) -> None:
        self.ollama_url = ollama_url
        self.model = model
        self.max_tool_calls = max_tool_calls
        self.summary_issue_limit = summary_issue_limit
        self.teams = teams

    async def answer(self, request: AgentRequest, jira: JiraClient) -> AgentResponse:
        team = self.teams.get(request.team_key.upper())
        if team is None:
            raise JiraError(f"Unknown team: {request.team_key}", 400)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(team)},
            *[{"role": item.role, "content": item.content} for item in request.history[-10:]],
            {"role": "user", "content": request.message},
        ]
        last_search: SearchResponse | None = None
        warnings: list[str] = []
        tool_calls_used = 0

        while tool_calls_used < self.max_tool_calls:
            assistant = await self._chat(messages)
            messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not calls:
                answer = str(assistant.get("content") or "I could not produce an answer from Jira.")
                return self._response(answer, last_search, warnings)

            for call in calls:
                if tool_calls_used >= self.max_tool_calls:
                    break
                tool_calls_used += 1
                function = call.get("function") or {}
                name = function.get("name")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                if name == "search_issues":
                    jql = scope_jql(str(arguments.get("jql") or ""), team.jira_filter)
                    logger.info("[AIM] Ollama selected Jira search: jql=%s", jql)
                    last_search = await jira.search_issues(
                        jql=jql,
                        page=1,
                        page_size=self.summary_issue_limit,
                    )
                    logger.info("[AIM] Jira evidence returned to Ollama: total=%s", last_search.total)
                    result: Any = last_search.model_dump()
                    if last_search.total > self.summary_issue_limit:
                        warnings = [
                            f"The answer summarizes the {self.summary_issue_limit} most relevant issues out of {last_search.total}."
                        ]
                elif name == "get_issue":
                    issue_key = str(arguments.get("issue_key") or "")
                    logger.info("[AIM] Ollama selected Jira issue lookup: issue=%s", issue_key)
                    result = (await jira.get_issue(issue_key)).model_dump()
                    logger.info("[AIM] Jira issue evidence returned to Ollama: issue=%s", issue_key)
                else:
                    result = {"error": f"Unknown or disallowed tool: {name}"}

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": str(name or "unknown"),
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        messages.append(
            {
                "role": "system",
                "content": "Tool-call limit reached. Answer now using only the Jira evidence already returned.",
            }
        )
        assistant = await self._chat(messages, include_tools=False)
        answer = str(assistant.get("content") or "The tool-call limit was reached before an answer was produced.")
        return self._response(answer, last_search, warnings)

    def _response(
        self,
        answer: str,
        search: SearchResponse | None,
        warnings: list[str],
    ) -> AgentResponse:
        return AgentResponse(
            answer=answer,
            jql=search.jql if search else "",
            total=search.total if search else None,
            issues=search.issues if search else [],
            warnings=warnings,
        )

    async def _chat(self, messages: list[dict[str, Any]], include_tools: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0},
        }
        if include_tools:
            payload["tools"] = TOOLS
        started = time.monotonic()
        logger.info("[AIM] Ollama request started: model=%s tools=%s", self.model, include_tools)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{self.ollama_url}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            logger.warning("[AIM] Ollama request failed after %.1fs", time.monotonic() - started)
            raise OllamaError(
                "Ollama is unavailable. Start Ollama and ensure the configured model is installed. Manual JQL still works."
            ) from exc
        logger.info(
            "[AIM] Ollama response received: status=%s elapsed=%.1fs",
            response.status_code,
            time.monotonic() - started,
        )
        if response.is_error:
            raise OllamaError(f"Ollama returned an error ({response.status_code}). Check that model '{self.model}' is installed.")
        try:
            return response.json().get("message") or {}
        except ValueError as exc:
            raise OllamaError("Ollama returned an invalid response.") from exc

    @staticmethod
    def _system_prompt(team: TeamInfo) -> str:
        aliases = ", ".join(team.aliases)
        verification = "verified" if team.verified else "UNVERIFIED; display this caveat if it affects the answer"
        return f"""You are the AIM Jira Assistant, a read-only Jira assistant for the AIM team at RADCOM.
Use tools for every factual Jira answer. Never invent issues, counts, statuses, people, or JQL results.
The active team is {team.display_name}. Its configured Jira filter is: {team.jira_filter}
Team aliases: {aliases}. This mapping is {verification}.
For searches, provide only the requested predicates; the backend always enforces the active team filter.
Use Jira Data Center JQL syntax. For unassigned issues use `assignee is EMPTY`.
Return concise answers grounded in tool results. Mention issue keys and make counts clear.
You are read-only. Never claim to create, edit, transition, comment on, or delete Jira data.
If the question is ambiguous, explain the interpretation used in the generated JQL.
"""
