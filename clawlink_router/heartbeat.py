"""ClawLink Router - Heartbeat monitor (async background task)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from clawlink_router.agents import AgentRegistry

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 15
DEFAULT_TIMEOUT_SECONDS = 5


class HeartbeatMonitor:
    """Periodically pings all registered agents and updates their alive status.

    Runs as an ``asyncio.Task`` in the background.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        ping_fn: Optional[Any] = None,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._timeout = timeout
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        # ping_fn(endpoint) -> bool; injectable for testing
        self._ping_fn = ping_fn or self._default_ping

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background heartbeat loop."""
        if self._running:
            logger.warning("HeartbeatMonitor is already running")
            return
        self._running = True
        self._task = asyncio.ensure_future(self._loop())
        logger.info(
            "HeartbeatMonitor started (interval=%ss, timeout=%ss)",
            self._interval,
            self._timeout,
        )

    async def stop(self) -> None:
        """Gracefully stop the heartbeat loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug("Heartbeat task cancelled")
            self._task = None
        logger.info("HeartbeatMonitor stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Continuously ping all agents at the configured interval."""
        while self._running:
            try:
                await self._ping_all()
            except Exception:
                logger.exception("Error during heartbeat cycle")
            await asyncio.sleep(self._interval)

    async def _ping_all(self) -> None:
        """Ping every registered agent and update status."""
        agents = self._registry.list_all()
        if not agents:
            return

        results = await asyncio.gather(
            *(self._ping_agent(a.endpoint) for a in agents),
            return_exceptions=True,
        )

        for agent, result in zip(agents, results):
            alive = isinstance(result, bool) and result
            self._registry.update_status(agent.agent_id, alive)
            if not alive:
                logger.warning("Agent %s (%s) is unreachable", agent.agent_id, agent.endpoint)

    async def _ping_agent(self, endpoint: str) -> bool:
        """Ping a single agent endpoint. Returns True if reachable."""
        try:
            return await asyncio.wait_for(
                self._do_ping(endpoint), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            logger.debug("Ping timed out for %s", endpoint)
            return False
        except Exception:
            logger.debug("Ping failed for %s", endpoint, exc_info=True)
            return False

    async def _do_ping(self, endpoint: str) -> bool:
        """Invoke the configured ping function."""
        result = self._ping_fn(endpoint)
        if asyncio.iscoroutine(result):
            return await result  # type: ignore[misc]
        return result  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Default ping implementation (HTTP GET /health)
    # ------------------------------------------------------------------

    @staticmethod
    async def _default_ping(endpoint: str) -> bool:
        """HTTP GET <endpoint>/health expecting a 200."""
        import httpx

        url = endpoint.rstrip("/") + "/health"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=DEFAULT_TIMEOUT_SECONDS)
            return resp.status_code == 200
