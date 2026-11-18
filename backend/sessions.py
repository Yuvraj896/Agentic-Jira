from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from backend.models import JiraUser


@dataclass
class JiraSession:
    pat: str
    user: JiraUser
    last_seen: float


class SessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, JiraSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, pat: str, user: JiraUser) -> str:
        session_id = secrets.token_urlsafe(32)
        async with self._lock:
            self._remove_expired(time.monotonic())
            self._sessions[session_id] = JiraSession(pat=pat, user=user, last_seen=time.monotonic())
        return session_id

    async def get(self, session_id: str) -> JiraSession | None:
        now = time.monotonic()
        async with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(session_id)
            if session:
                session.last_seen = now
            return session

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    def _remove_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_seen >= self.ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
