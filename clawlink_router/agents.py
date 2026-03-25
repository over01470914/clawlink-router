"""ClawLink Router - Central registry of connected agents."""

from __future__ import annotations

import logging
from typing import Optional

from clawlink_router.models import AgentInfo

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Thread-safe in-memory registry for all connected agents."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentInfo] = {}

    def register(self, agent: AgentInfo) -> bool:
        """Register an agent. Returns True on success, False if ID already taken."""
        if agent.agent_id in self._agents:
            logger.warning("Agent %s is already registered", agent.agent_id)
            return False
        self._agents[agent.agent_id] = agent
        logger.info("Registered agent %s (%s)", agent.agent_id, agent.display_name)
        return True

    def unregister(self, agent_id: str) -> bool:
        """Remove an agent from the registry. Returns True if it existed."""
        if agent_id not in self._agents:
            logger.warning("Attempted to unregister unknown agent %s", agent_id)
            return False
        del self._agents[agent_id]
        logger.info("Unregistered agent %s", agent_id)
        return True

    def get(self, agent_id: str) -> Optional[AgentInfo]:
        """Return agent info or None."""
        return self._agents.get(agent_id)

    def list_all(self) -> list[AgentInfo]:
        """Return every registered agent (alive or not)."""
        return list(self._agents.values())

    def list_alive(self) -> list[AgentInfo]:
        """Return only agents whose heartbeat is still alive."""
        return [a for a in self._agents.values() if a.is_alive]

    def update_status(self, agent_id: str, is_alive: bool) -> None:
        """Set the alive flag for an agent."""
        agent = self._agents.get(agent_id)
        if agent is None:
            logger.warning("Cannot update status for unknown agent %s", agent_id)
            return
        agent.is_alive = is_alive
        logger.debug("Agent %s alive=%s", agent_id, is_alive)

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents
