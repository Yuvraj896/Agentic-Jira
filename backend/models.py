from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class JiraUser(BaseModel):
    username: str = ""
    display_name: str
    email: str = ""


class ConnectRequest(BaseModel):
    pat: SecretStr


class ConnectionResponse(BaseModel):
    user: JiraUser
    expires_in_seconds: int


class SessionStatus(BaseModel):
    connected: bool
    user: JiraUser


class TeamInfo(BaseModel):
    key: str
    display_name: str
    jira_filter: str
    aliases: list[str] = Field(default_factory=list)
    verified: bool = False


class FieldInfo(BaseModel):
    id: str
    name: str
    custom: bool = False


class Comment(BaseModel):
    id: str
    author: str
    body: str
    created: str = ""
    updated: str = ""


class IssueSummary(BaseModel):
    key: str
    summary: str
    status: str = ""
    priority: str = ""
    assignee: str = "Unassigned"
    issue_type: str = ""
    project: str = ""
    created: str = ""
    updated: str = ""
    url: str


class IssueDetail(IssueSummary):
    description: str = ""
    reporter: str = ""
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    jql: str = Field(min_length=1, max_length=2000)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)

    @field_validator("jql")
    @classmethod
    def clean_jql(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("JQL cannot be blank")
        if ";" in value:
            raise ValueError("JQL must contain one query without semicolons")
        return value


class SearchResponse(BaseModel):
    jql: str
    total: int
    page: int
    page_size: int
    issues: list[IssueSummary]


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=8000)

    @field_validator("role")
    @classmethod
    def supported_role(cls, value: str) -> str:
        if value not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        return value


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)
    team_key: str = "AIM"


class ChartPoint(BaseModel):
    label: str
    value: int


class Visualization(BaseModel):
    type: Literal["bar", "pie"]
    title: str
    data: list[ChartPoint]


class AgentResponse(BaseModel):
    answer: str
    jql: str = ""
    total: int | None = None
    issues: list[IssueSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    visualization: Visualization | None = None
