"""ClawLink Router - Data models for sessions, agents, messages, and scoring."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AuthMethod(str, Enum):
    """Supported authentication methods for agent connections."""
    SSH = "ssh"
    API_KEY = "api_key"
    MTLS = "mtls"
    OAUTH = "oauth"
    PAIRING_CODE = "pairing_code"


class ConversationMode(str, Enum):
    """Direction of the teaching relationship in a solo session."""
    A_LEADS = ">"
    DISCUSSION = "|"
    B_LEADS = "<"


class SessionStatus(str, Enum):
    """Lifecycle status of a session."""
    PENDING = "pending"
    ACTIVE = "active"
    SCORING = "scoring"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CLOSED = "closed"


class MessageType(str, Enum):
    """Classification of messages flowing through the router."""
    TEACHING = "teaching"
    RESPONSE = "response"
    SUMMARY = "summary"
    CHALLENGE = "challenge"
    FEEDBACK = "feedback"
    SELF_ASSESSMENT = "self_assessment"
    SYSTEM = "system"
    MEMORY_CMD = "memory_cmd"
    USER = "user"
    QUEUED = "queued"


class ChatType(str, Enum):
    """Whether a session is one-on-one or a group conversation."""
    SOLO = "solo"
    GROUP = "group"


# ---------------------------------------------------------------------------
# Agent / Connection models
# ---------------------------------------------------------------------------

class AgentInfo(BaseModel):
    """Metadata describing a single connected agent."""
    agent_id: str
    display_name: str
    agent_type: str = Field(
        default="openclaw",
        pattern=r"^(openclaw|local|remote)$",
        description="One of openclaw, local, remote.",
    )
    endpoint: str
    avatar_color: str = Field(
        default="#5865F2",
        description="Hex colour for UI rendering.",
    )
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_alive: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectionConfig(BaseModel):
    """Configuration blob supplied when an agent connects."""
    endpoint: str
    auth_method: AuthMethod
    credentials: dict[str, Any] = Field(default_factory=dict)
    pairing_code: Optional[str] = None


class PairingCode(BaseModel):
    """A short-lived XXXX-XXXX code used for easy agent pairing."""
    code: str = Field(description="Format XXXX-XXXX.")
    agent_endpoint: str
    agent_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session / Message models
# ---------------------------------------------------------------------------

class SessionConfig(BaseModel):
    """Initial configuration for creating a session."""
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    chat_type: ChatType = ChatType.SOLO
    mode: ConversationMode = ConversationMode.DISCUSSION
    agents: list[str] = Field(default_factory=list, description="Agent IDs participating.")
    strictness: int = Field(default=50, ge=0, le=100)
    pass_threshold: int = 70
    max_iterations: int = 10
    rubric: Optional[dict[str, Any]] = None


class Message(BaseModel):
    """A single message in a session."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    from_id: str = Field(description="Agent ID or 'user'.")
    to_id: Optional[str] = None
    content: str
    message_type: MessageType = MessageType.USER
    confidence: Optional[float] = None
    mentions: list[str] = Field(default_factory=list, description="@mentioned agent IDs.")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueuedMessage(BaseModel):
    """Wrapper around a message that lives in the queue."""
    message: Message
    position: int
    status: str = Field(default="waiting", pattern=r"^(waiting|delivered|processing)$")


class SelfAssessment(BaseModel):
    """An agent's self-reported confidence and rubric scores."""
    agent_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    rubric_scores: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""


class ScoreResult(BaseModel):
    """Result of a teaching-round scoring cycle."""
    score: float = Field(ge=0, le=100)
    passed: bool
    feedback: str
    rubric_details: dict[str, Any] = Field(default_factory=dict)
    teacher_listened: bool = False
    student_challenge_accepted: bool = False


# ---------------------------------------------------------------------------
# File-locking models
# ---------------------------------------------------------------------------

class FileLock(BaseModel):
    """Active lock on a file path."""
    file_path: str
    locked_by: str
    locked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: Optional[str] = None


class LockStatus(BaseModel):
    """Query result for a file-lock check."""
    is_locked: bool
    locked_by: Optional[str] = None
    wait_queue: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Session state aggregate
# ---------------------------------------------------------------------------

class SessionState(BaseModel):
    """Full snapshot of a session's runtime state."""
    session_id: str
    chat_type: ChatType
    mode: Optional[ConversationMode] = None
    status: SessionStatus = SessionStatus.PENDING
    agents: list[AgentInfo] = Field(default_factory=list)
    strictness: int = 50
    pass_threshold: int = 70
    max_iterations: int = 10
    current_iteration: int = 0
    messages: list[Message] = Field(default_factory=list)
    message_queue: list[QueuedMessage] = Field(default_factory=list)
    scores: list[ScoreResult] = Field(default_factory=list)
    active_locks: list[FileLock] = Field(default_factory=list)
    heartbeat_status: dict[str, bool] = Field(default_factory=dict)
    rubric: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Group-chat models
# ---------------------------------------------------------------------------

class GroupChatTopic(BaseModel):
    """A named topic / thread inside a group session."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    messages: list[Message] = Field(default_factory=list)


class MemoryFile(BaseModel):
    """Metadata for a generated markdown memory file."""
    path: str
    topic: str
    teacher: str
    student: str
    final_score: float = 0
    key_decisions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TeachingMetrics(BaseModel):
    """Aggregated statistics across teaching sessions."""
    total_sessions: int = 0
    avg_convergence: float = 0.0
    error_rate: float = 0.0
    avg_score: float = 0.0
    challenge_rate: float = 0.0
