"""ClawLink Router - Session lifecycle management."""

from __future__ import annotations

import logging
from typing import Optional

from clawlink_router.models import (
    AgentInfo,
    ChatType,
    ConversationMode,
    Message,
    SessionConfig,
    SessionState,
    SessionStatus,
)

logger = logging.getLogger(__name__)


class SessionManager:
    """Create, query, update, and delete router sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, config: SessionConfig, agents: list[AgentInfo]) -> SessionState:
        """Create a new session from a config and a list of resolved AgentInfo objects."""
        state = SessionState(
            session_id=config.session_id,
            chat_type=config.chat_type,
            mode=config.mode if config.chat_type == ChatType.SOLO else None,
            status=SessionStatus.PENDING,
            agents=agents,
            strictness=config.strictness,
            pass_threshold=config.pass_threshold,
            max_iterations=config.max_iterations,
            heartbeat_status={a.agent_id: True for a in agents},
            rubric=config.rubric,
        )
        self._sessions[state.session_id] = state
        logger.info(
            "Created %s session %s with %d agent(s)",
            config.chat_type.value,
            state.session_id,
            len(agents),
        )
        return state

    def get(self, session_id: str) -> Optional[SessionState]:
        """Return a session state or None."""
        return self._sessions.get(session_id)

    def list_all(self) -> list[SessionState]:
        """Return every known session."""
        return list(self._sessions.values())

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        if session_id not in self._sessions:
            logger.warning("Attempted to delete unknown session %s", session_id)
            return False
        del self._sessions[session_id]
        logger.info("Deleted session %s", session_id)
        return True

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def set_status(self, session_id: str, status: SessionStatus) -> bool:
        """Transition a session to a new status."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        old = session.status
        session.status = status
        logger.info("Session %s: %s -> %s", session_id, old.value, status.value)
        return True

    # ------------------------------------------------------------------
    # Strictness
    # ------------------------------------------------------------------

    def update_strictness(self, session_id: str, strictness: int) -> bool:
        """Update the strictness level (0-100) for a session."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        strictness = max(0, min(100, strictness))
        session.strictness = strictness
        logger.info("Session %s strictness set to %d", session_id, strictness)
        return True

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(self, session_id: str, message: Message) -> bool:
        """Append a message to the session's message list."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.messages.append(message)
        return True

    # ------------------------------------------------------------------
    # Heartbeat per-agent
    # ------------------------------------------------------------------

    def update_heartbeat(self, session_id: str, agent_id: str, alive: bool) -> bool:
        """Record a heartbeat status for one agent in a session."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.heartbeat_status[agent_id] = alive
        return True

    def get_heartbeat(self, session_id: str) -> dict[str, bool]:
        """Return the per-agent heartbeat dict for a session."""
        session = self._sessions.get(session_id)
        if session is None:
            return {}
        return dict(session.heartbeat_status)

    # ------------------------------------------------------------------
    # Iteration tracking
    # ------------------------------------------------------------------

    def increment_iteration(self, session_id: str) -> int:
        """Bump the current iteration counter and return its new value."""
        session = self._sessions.get(session_id)
        if session is None:
            return -1
        session.current_iteration += 1
        return session.current_iteration
