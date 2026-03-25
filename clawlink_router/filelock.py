"""ClawLink Router - Sealed-zone file lock manager."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from clawlink_router.models import FileLock, LockStatus

logger = logging.getLogger(__name__)


class FileLockManager:
    """In-router file locking with wait queues.

    Only one agent may hold the lock on a given path at a time.  Other
    agents can join a wait queue and will be notified (via an ``asyncio.Event``)
    when the lock becomes available.
    """

    def __init__(self) -> None:
        self._locks: dict[str, FileLock] = {}
        self._wait_queues: dict[str, list[str]] = defaultdict(list)
        self._events: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, file_path: str, agent_id: str, reason: Optional[str] = None) -> bool:
        """Try to acquire the lock on *file_path*.

        Returns True if the lock was granted, False if it is held by another
        agent (the caller is automatically added to the wait queue).
        """
        existing = self._locks.get(file_path)
        if existing is not None:
            if existing.locked_by == agent_id:
                logger.debug("Agent %s already holds lock on %s", agent_id, file_path)
                return True
            if agent_id not in self._wait_queues[file_path]:
                self._wait_queues[file_path].append(agent_id)
                logger.info(
                    "Agent %s queued for lock on %s (held by %s)",
                    agent_id,
                    file_path,
                    existing.locked_by,
                )
            return False

        lock = FileLock(
            file_path=file_path,
            locked_by=agent_id,
            locked_at=datetime.now(timezone.utc),
            reason=reason,
        )
        self._locks[file_path] = lock
        logger.info("Agent %s acquired lock on %s", agent_id, file_path)
        return True

    def release(self, file_path: str, agent_id: str) -> bool:
        """Release a lock held by *agent_id*.

        Returns False if the lock does not exist or is held by a different agent.
        If waiters exist the lock is automatically handed to the first in line.
        """
        lock = self._locks.get(file_path)
        if lock is None:
            logger.warning("No lock exists on %s", file_path)
            return False
        if lock.locked_by != agent_id:
            logger.warning(
                "Agent %s cannot release lock on %s (held by %s)",
                agent_id,
                file_path,
                lock.locked_by,
            )
            return False

        del self._locks[file_path]
        logger.info("Agent %s released lock on %s", agent_id, file_path)

        # Hand off to next waiter
        waiters = self._wait_queues.get(file_path, [])
        if waiters:
            next_agent = waiters.pop(0)
            self.acquire(file_path, next_agent)
            event = self._events.pop(file_path, None)
            if event is not None:
                event.set()
        return True

    def check(self, file_path: str) -> LockStatus:
        """Return the lock status for *file_path*."""
        lock = self._locks.get(file_path)
        if lock is None:
            return LockStatus(is_locked=False)
        return LockStatus(
            is_locked=True,
            locked_by=lock.locked_by,
            wait_queue=list(self._wait_queues.get(file_path, [])),
        )

    async def wait(self, file_path: str, agent_id: str, timeout: float = 30.0) -> bool:
        """Block until the lock on *file_path* becomes available (up to *timeout* seconds).

        The caller is added to the wait queue automatically.  Returns True if
        the lock was acquired, False on timeout.
        """
        if self.acquire(file_path, agent_id):
            return True

        event = self._events.setdefault(file_path, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Agent %s timed out waiting for lock on %s", agent_id, file_path)
            self._wait_queues.get(file_path, [])
            return False

        return file_path in self._locks and self._locks[file_path].locked_by == agent_id

    def force_release(self, file_path: str) -> bool:
        """Forcefully release a lock regardless of who holds it."""
        lock = self._locks.get(file_path)
        if lock is None:
            return False
        holder = lock.locked_by
        del self._locks[file_path]
        logger.warning("Force-released lock on %s (was held by %s)", file_path, holder)

        waiters = self._wait_queues.get(file_path, [])
        if waiters:
            next_agent = waiters.pop(0)
            self.acquire(file_path, next_agent)
            event = self._events.pop(file_path, None)
            if event is not None:
                event.set()
        return True

    def list_locks(self) -> list[FileLock]:
        """Return all active locks."""
        return list(self._locks.values())

    def agent_locks(self, agent_id: str) -> list[FileLock]:
        """Return all locks held by a specific agent."""
        return [l for l in self._locks.values() if l.locked_by == agent_id]

    def release_all_for_agent(self, agent_id: str) -> int:
        """Release every lock held by an agent (e.g. on disconnect). Returns count."""
        paths = [l.file_path for l in self._locks.values() if l.locked_by == agent_id]
        for p in paths:
            self.release(p, agent_id)
        return len(paths)
