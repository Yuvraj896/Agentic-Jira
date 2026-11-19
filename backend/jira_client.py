from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from backend.models import (
    Comment,
    FieldInfo,
    IssueDetail,
    IssueSummary,
    JiraUser,
    SearchResponse,
)


logger = logging.getLogger("uvicorn.error")
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$", re.IGNORECASE)
SEARCH_FIELDS = [
    "summary",
    "status",
    "priority",
    "assignee",
    "issuetype",
    "project",
    "created",
    "updated",
]


class JiraError(RuntimeError):
    def __init__(self, public_message: str, status_code: int = 502) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code


def _named(value: Any, default: str = "") -> str:
    if not isinstance(value, dict):
        return default
    for key in ("displayName", "name", "value", "key", "id"):
        if value.get(key) not in (None, ""):
            return str(value[key])
    return default


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(part for part in (_plain_text(item) for item in value) if part)
    if isinstance(value, dict):
        if value.get("text"):
            return str(value["text"])
        named = _named(value)
        if named:
            return named
        if isinstance(value.get("content"), list):
            return " ".join(part for part in (_plain_text(item) for item in value["content"]) if part)
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


class JiraClient:
    def __init__(
        self,
        base_url: str,
        pat: str,
        verify_ssl: bool,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._pat = pat
        self.verify_ssl = verify_ssl
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/rest/api/2",
            headers={
                "Authorization": f"Bearer {self._pat}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            verify=self.verify_ssl,
            timeout=self.timeout_seconds,
            transport=self.transport,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        started = time.monotonic()
        logger.info("[AIM] Jira API request started: %s /rest/api/2%s", method, path)
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            logger.warning("[AIM] Jira API timed out: %s /rest/api/2%s", method, path)
            raise JiraError("Jira did not respond before the request timed out.", 504) from exc
        except httpx.HTTPError as exc:
            logger.warning("[AIM] Jira API connection failed: %s /rest/api/2%s", method, path)
            raise JiraError("Could not reach RADCOM Jira. Check the network or VPN connection.", 502) from exc

        logger.info(
            "[AIM] Jira API response received: %s /rest/api/2%s status=%s elapsed=%.1fs",
            method,
            path,
            response.status_code,
            time.monotonic() - started,
        )

        if response.status_code in {401, 403}:
            raise JiraError("Jira rejected this PAT or the user lacks permission.", 401)
        if response.status_code == 404:
            raise JiraError("The requested Jira resource was not found.", 404)
        if response.status_code == 429:
            raise JiraError("Jira rate-limited the request. Wait briefly and try again.", 429)
        if response.is_error:
            detail = ""
            try:
                body = response.json()
                detail = " ".join(body.get("errorMessages") or []) or str(body.get("errors") or "")
            except ValueError:
                pass
            message = f"Jira could not process the request ({response.status_code})."
            if detail:
                message = f"{message} {detail}"
            raise JiraError(message, 400 if response.status_code < 500 else 502)
        try:
            return response.json()
        except ValueError as exc:
            raise JiraError("Jira returned an invalid response.", 502) from exc

    async def get_current_user(self) -> JiraUser:
        data = await self._request("GET", "/myself")
        return JiraUser(
            username=str(data.get("name") or data.get("key") or ""),
            display_name=str(data.get("displayName") or data.get("name") or "Jira user"),
            email=str(data.get("emailAddress") or ""),
        )

    async def get_fields(self) -> list[FieldInfo]:
        data = await self._request("GET", "/field")
        return [
            FieldInfo(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                custom=bool(item.get("custom", False)),
            )
            for item in data
            if item.get("id") and item.get("name")
        ]

    async def find_assignable_users(
        self,
        search_text: str,
        project: str,
        max_results: int = 10,
    ) -> list[JiraUser]:
        search_text = search_text.strip()
        if not search_text:
            return []
        data = await self._request(
            "GET",
            "/user/assignable/search",
            params={
                "username": search_text,
                "project": project,
                "maxResults": min(50, max(1, max_results)),
            },
        )
        if not isinstance(data, list):
            raise JiraError("Jira returned an invalid user search response.", 502)
        return [
            JiraUser(
                username=str(item.get("name") or item.get("key") or ""),
                display_name=str(item.get("displayName") or item.get("name") or "Jira user"),
                email=str(item.get("emailAddress") or ""),
            )
            for item in data
            if isinstance(item, dict)
        ]

    async def search_issues(self, jql: str, page: int = 1, page_size: int = 25) -> SearchResponse:
        jql = jql.strip()
        if not jql or len(jql) > 2000 or ";" in jql:
            raise JiraError("The generated JQL is empty or invalid.", 400)
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        data = await self._request(
            "POST",
            "/search",
            json={
                "jql": jql,
                "startAt": (page - 1) * page_size,
                "maxResults": page_size,
                "fields": SEARCH_FIELDS,
            },
        )
        issues = [self._issue_summary(item) for item in data.get("issues") or []]
        return SearchResponse(
            jql=jql,
            total=int(data.get("total") or len(issues)),
            page=page,
            page_size=page_size,
            issues=issues,
        )

    async def get_issue(self, issue_key: str) -> IssueDetail:
        issue_key = issue_key.strip().upper()
        if not ISSUE_KEY_RE.fullmatch(issue_key):
            raise JiraError("Invalid Jira issue key.", 400)
        data = await self._request("GET", f"/issue/{issue_key}", params={"expand": "names"})
        comments_data = await self._request(
            "GET",
            f"/issue/{issue_key}/comment",
            params={"startAt": 0, "maxResults": 100},
        )
        fields = data.get("fields") or {}
        names = data.get("names") or {}
        summary = self._issue_summary(data)
        custom_fields: dict[str, Any] = {}
        for field_id, value in fields.items():
            if not field_id.startswith("customfield_") or value in (None, "", []):
                continue
            custom_fields[str(names.get(field_id) or field_id)] = _plain_text(value)

        comments = [
            Comment(
                id=str(item.get("id") or ""),
                author=_named(item.get("author"), "Unknown"),
                body=_plain_text(item.get("body")),
                created=str(item.get("created") or ""),
                updated=str(item.get("updated") or ""),
            )
            for item in comments_data.get("comments") or []
        ]
        return IssueDetail(
            **summary.model_dump(),
            description=_plain_text(fields.get("description")),
            reporter=_named(fields.get("reporter")),
            labels=[str(item) for item in fields.get("labels") or []],
            components=[_named(item) for item in fields.get("components") or [] if _named(item)],
            comments=comments,
            fields=custom_fields,
        )

    def _issue_summary(self, item: dict[str, Any]) -> IssueSummary:
        fields = item.get("fields") or {}
        key = str(item.get("key") or "")
        return IssueSummary(
            key=key,
            summary=str(fields.get("summary") or ""),
            status=_named(fields.get("status")),
            priority=_named(fields.get("priority")),
            assignee=_named(fields.get("assignee"), "Unassigned"),
            issue_type=_named(fields.get("issuetype")),
            project=_named(fields.get("project")),
            created=str(fields.get("created") or ""),
            updated=str(fields.get("updated") or ""),
            url=f"{self.base_url}/browse/{key}",
        )
