"""ClawLink Router - Group chat management with @mention routing."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from clawlink_router.models import GroupChatTopic, Message

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"@([\w\-]+)")


class GroupChatManager:
    """Manage group-chat topics and route messages via @mentions."""

    def __init__(self) -> None:
        self._topics: dict[str, list[GroupChatTopic]] = {}  # session_id -> topics
        self._messages: dict[str, list[Message]] = {}       # session_id -> all msgs

    # ------------------------------------------------------------------
    # Topics
    # ------------------------------------------------------------------

    def create_topic(
        self, session_id: str, title: str, created_by: str
    ) -> GroupChatTopic:
        """Create a new conversation topic inside a group session."""
        topic = GroupChatTopic(title=title, created_by=created_by)
        self._topics.setdefault(session_id, []).append(topic)
        logger.info(
            "Created topic '%s' (id=%s) in session %s by %s",
            title,
            topic.id,
            session_id,
            created_by,
        )
        return topic

    def list_topics(self, session_id: str) -> list[GroupChatTopic]:
        """Return all topics for a session."""
        return list(self._topics.get(session_id, []))

    def get_topic_messages(self, topic_id: str) -> list[Message]:
        """Return messages belonging to a specific topic."""
        for topics in self._topics.values():
            for topic in topics:
                if topic.id == topic_id:
                    return list(topic.messages)
        return []

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    def route_message(self, session_id: str, message: Message) -> list[str]:
        """Route a message based on @mentions in its content.

        * If the message contains @agent_id mentions, only deliver to those agents.
        * If there are no @mentions the message is "silent" -- stored but not
          actively pushed to any agent.

        Returns the list of agent_ids the message was delivered to.
        """
        self._messages.setdefault(session_id, []).append(message)

        # Also append to topic if a topic_id is stored in metadata
        topic_id = message.metadata.get("topic_id")
        if topic_id:
            self._append_to_topic(topic_id, message)

        # Parse @mentions from content
        mentioned = _MENTION_RE.findall(message.content)
        # Also honour the structured mentions field
        all_mentions = list(set(mentioned) | set(message.mentions))

        if not all_mentions:
            logger.debug(
                "Message %s in session %s is silent (no @mentions)",
                message.id,
                session_id,
            )
            return []

        # Update the message's mentions field to include parsed ones
        message.mentions = all_mentions
        logger.info(
            "Routing message %s to %s in session %s",
            message.id,
            all_mentions,
            session_id,
        )
        return all_mentions

    def fetch_messages(
        self,
        session_id: str,
        agent_id: str,
        since: Optional[datetime] = None,
    ) -> list[Message]:
        """Fetch ALL messages in the group for a given session.

        An agent can see every message (even ones not @mentioned to them).
        Optionally filter by timestamp.
        """
        msgs = self._messages.get(session_id, [])
        if since is not None:
            since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            msgs = [m for m in msgs if m.timestamp >= since_utc]
        logger.debug(
            "Agent %s fetched %d message(s) from session %s",
            agent_id,
            len(msgs),
            session_id,
        )
        return msgs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_to_topic(self, topic_id: str, message: Message) -> None:
        """Attach a message to its topic if found."""
        for topics in self._topics.values():
            for topic in topics:
                if topic.id == topic_id:
                    topic.messages.append(message)
                    return

    def add_message(self, session_id: str, message: Message) -> None:
        """Store a message without routing (used by the router internally)."""
        self._messages.setdefault(session_id, []).append(message)
