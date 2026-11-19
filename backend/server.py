from __future__ import annotations

import logging
import os
from pathlib import Path
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.agent import AgentService, OpenAIError
from backend.config import Settings, load_settings
from backend.jira_client import JiraClient, JiraError
from backend.models import (
    AgentRequest,
    AgentResponse,
    ConnectRequest,
    ConnectionResponse,
    FieldInfo,
    IssueDetail,
    SearchRequest,
    SearchResponse,
    SessionStatus,
    TeamInfo,
)
from backend.sessions import JiraSession, SessionStore
from backend.teams import load_teams


COOKIE_NAME = "jira_live_session"
logger = logging.getLogger("uvicorn.error")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    teams = load_teams(settings.teams_config)
    app = FastAPI(title="AIM Jira Assistant", version="0.1.0")
    app.state.settings = settings
    app.state.sessions = SessionStore(settings.session_ttl_seconds)
    app.state.teams = teams
    app.state.jira_client_factory = lambda pat: JiraClient(
        base_url=settings.jira_base_url,
        pat=pat,
        verify_ssl=settings.jira_verify_ssl,
        timeout_seconds=settings.jira_timeout_seconds,
    )
    app.state.agent = AgentService(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        teams=teams,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def require_session(
        session_id: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> JiraSession:
        if not session_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connect to Jira first.")
        session = await app.state.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Jira session expired.")
        return session

    def client_for(session: JiraSession) -> JiraClient:
        return app.state.jira_client_factory(session.pat)

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "service": "jira-live",
            "jira_base_url": settings.jira_base_url,
            "llm_provider": "openai",
            "openai_model": settings.openai_model,
            "openai_configured": bool(settings.openai_api_key),
        }

    @app.post("/api/session/connect", response_model=ConnectionResponse)
    async def connect(payload: ConnectRequest, response: Response) -> ConnectionResponse:
        pat = payload.pat.get_secret_value().strip()
        if not pat:
            raise HTTPException(status_code=422, detail="A Jira personal access token is required.")
        logger.info("[AIM] Jira authentication started: GET /rest/api/2/myself")
        try:
            user = await app.state.jira_client_factory(pat).get_current_user()
        except JiraError as exc:
            logger.warning("[AIM] Jira authentication failed: %s", exc.public_message)
            raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
        logger.info("[AIM] Jira authentication succeeded: user=%s", user.display_name)

        session_id = await app.state.sessions.create(pat=pat, user=user)
        response.set_cookie(
            key=COOKIE_NAME,
            value=session_id,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )
        return ConnectionResponse(user=user, expires_in_seconds=settings.session_ttl_seconds)

    @app.delete("/api/session", status_code=204)
    async def disconnect(
        response: Response,
        session_id: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> None:
        if session_id:
            await app.state.sessions.delete(session_id)
        response.delete_cookie(COOKIE_NAME, path="/")

    @app.get("/api/session", response_model=SessionStatus)
    async def session_status(session: JiraSession = Depends(require_session)) -> SessionStatus:
        return SessionStatus(connected=True, user=session.user)

    @app.get("/api/teams", response_model=list[TeamInfo])
    async def list_teams() -> list[TeamInfo]:
        return [TeamInfo(**team.model_dump()) for team in teams.values()]

    @app.get("/api/jira/fields", response_model=list[FieldInfo])
    async def fields(session: JiraSession = Depends(require_session)) -> list[FieldInfo]:
        try:
            return await client_for(session).get_fields()
        except JiraError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc

    @app.post("/api/jira/search", response_model=SearchResponse)
    async def search(
        payload: SearchRequest,
        session: JiraSession = Depends(require_session),
    ) -> SearchResponse:
        logger.info("[AIM] Manual Jira search started: jql=%s", payload.jql)
        try:
            result = await client_for(session).search_issues(
                jql=payload.jql,
                page=payload.page,
                page_size=payload.page_size,
            )
        except JiraError as exc:
            logger.warning("[AIM] Manual Jira search failed: %s", exc.public_message)
            raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
        logger.info("[AIM] Manual Jira search completed: total=%s", result.total)
        return result

    @app.get("/api/jira/issues/{issue_key}", response_model=IssueDetail)
    async def issue_detail(
        issue_key: str,
        session: JiraSession = Depends(require_session),
    ) -> IssueDetail:
        try:
            return await client_for(session).get_issue(issue_key)
        except JiraError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc

    @app.post("/api/agent/chat", response_model=AgentResponse)
    async def chat(
        payload: AgentRequest,
        session: JiraSession = Depends(require_session),
    ) -> AgentResponse:
        logger.info("[AIM] Agent request started: team=%s", payload.team_key)
        try:
            result = await app.state.agent.answer(payload, client_for(session))
        except JiraError as exc:
            logger.warning("[AIM] Agent Jira request failed: %s", exc.public_message)
            raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
        except OpenAIError as exc:
            logger.warning("[AIM] Agent OpenAI request failed: %s", exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        logger.info("[AIM] Agent request completed: total=%s", result.total)
        return result

    ui_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if ui_dist.exists() and os.environ.get("SERVE_UI", "true").lower() == "true":
        app.mount("/", StaticFiles(directory=ui_dist, html=True), name="ui")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.server:app",
        host=os.environ.get("JIRA_LIVE_HOST", "127.0.0.1"),
        port=int(os.environ.get("JIRA_LIVE_PORT", "8000")),
        reload=os.environ.get("JIRA_LIVE_RELOAD", "false").lower() == "true",
    )
