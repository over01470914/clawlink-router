"""ClawLink Router - User message queue (queues behind active agent exchanges)."""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from clawlink_router.models import Message, QueuedMessage

logger = logging.getLogger(__name__)


class MessageQueue:
    """Per-session FIFO queue that buffers user messages while agents converse.

    When agents are in a teaching / conversation flow, user messages do NOT
    interrupt.  They queue up and get delivered when agents finish their
    current exchange.
    """

    def __init__(self) -> None:
        self._queues: dict[str, deque[QueuedMessage]] = {}

    def _ensure_queue(self, session_id: str) -> deque[QueuedMessage]:
        """Lazily create a per-session deque."""
        if session_id not in self._queues:
            self._queues[session_id] = deque()
        return self._queues[session_id]

    def enqueue(self, session_id: str, message: Message) -> QueuedMessage:
        """Add a message to the tail of the session's queue."""
        q = self._ensure_queue(session_id)
        position = len(q) + 1
        queued = QueuedMessage(message=message, position=position, status="waiting")
        q.append(queued)
        logger.info(
            "Enqueued message %s in session %s at position %d",
            message.id,
            session_id,
            position,
        )
        return queued

    def dequeue(self, session_id: str) -> Optional[Message]:
        """Pop the next waiting message from the front of the queue.

        Returns None if the queue is empty or all messages have been consumed.
        """
        q = self._queues.get(session_id)
        if not q:
            return None
        while q:
            item = q[0]
            if item.status == "waiting":
                item.status = "delivered"
                q.popleft()
                logger.info(
                    "Dequeued message %s from session %s",
                    item.message.id,
                    session_id,
                )
                return item.message
            q.popleft()  # skip already-delivered leftovers
        return None

    def peek(self, session_id: str) -> list[QueuedMessage]:
        """View all items currently in the queue without consuming them."""
        q = self._queues.get(session_id)
        if not q:
            return []
        return list(q)

    def process_queue(self, session_id: str) -> list[Message]:
        """Drain the entire queue, returning all waiting messages in order."""
        q = self._queues.get(session_id)
        if not q:
            return []
        delivered: list[Message] = []
        while q:
            item = q.popleft()
            if item.status == "waiting":
                item.status = "delivered"
                delivered.append(item.message)
        logger.info(
            "Flushed %d message(s) from session %s queue",
            len(delivered),
            session_id,
        )
        return delivered

    def get_queue_length(self, session_id: str) -> int:
        """Return the number of waiting messages."""
        q = self._queues.get(session_id)
        if not q:
            return 0
        return sum(1 for item in q if item.status == "waiting")

    def clear(self, session_id: str) -> None:
        """Drop the queue for a session entirely."""
        self._queues.pop(session_id, None)
        logger.info("Cleared queue for session %s", session_id)
