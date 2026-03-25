"""ClawLink Router - FastAPI application with all endpoints."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from clawlink_router import __version__
from clawlink_router.agents import AgentRegistry
from clawlink_router.ai_client import GenericAIClient, MockAIClient
from clawlink_router.auth import AuthManager
from clawlink_router.filelock import FileLockManager
from clawlink_router.group_chat import GroupChatManager
from clawlink_router.heartbeat import HeartbeatMonitor
from clawlink_router.message_queue import MessageQueue
from clawlink_router.models import (
    AgentInfo,
    ChatType,
    ConnectionConfig,
    Message,
    MessageType,
    SessionConfig,
    SessionStatus,
    TeachingMetrics,
)
from clawlink_router.router import ConversationRouter
from clawlink_router.scoring import ScoringEngine
from clawlink_router.session import SessionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state (module-level singletons wired up in lifespan)
# ---------------------------------------------------------------------------
auth_manager = AuthManager()
agent_registry = AgentRegistry()
session_manager = SessionManager()
message_queue = MessageQueue()
group_chat_manager = GroupChatManager()
scoring_engine = ScoringEngine()
file_lock_manager = FileLockManager()
ai_client = GenericAIClient()
heartbeat_monitor = HeartbeatMonitor(registry=agent_registry)
conversation_router = ConversationRouter(
    session_manager=session_manager,
    agent_registry=agent_registry,
    ai_client=ai_client,
    message_queue=message_queue,
    group_chat=group_chat_manager,
    scoring_engine=scoring_engine,
)

# WebSocket connection manager
_ws_connections: dict[str, list[WebSocket]] = {}


async def _ws_broadcast(session_id: str, payload: dict[str, Any]) -> None:
    """Push a JSON payload to every WebSocket subscribed to *session_id*."""
    sockets = _ws_connections.get(session_id, [])
    dead: list[WebSocket] = []
    for ws in sockets:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        sockets.remove(ws)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[override]
    """Start background services on startup; tear down on shutdown."""
    heartbeat_monitor.start()
    logger.info("ClawLink Router v%s started", __version__)
    yield
    await heartbeat_monitor.stop()
    logger.info("ClawLink Router shut down")


app = FastAPI(
    title="ClawLink Router",
    version=__version__,
    description="Multi-agent middleware router with teaching loops, group chat, and message queuing.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ConnectRequest(BaseModel):
    endpoint: str
    auth_method: str
    credentials: dict[str, Any] = Field(default_factory=dict)
    pairing_code: Optional[str] = None


class PairGenerateRequest(BaseModel):
    agent_endpoint: str
    agent_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PairValidateRequest(BaseModel):
    code: str


class PairCompleteRequest(BaseModel):
    code: str
    agent_id: str
    display_name: str
    agent_type: str = "openclaw"
    endpoint: str = ""
    avatar_color: str = "#5865F2"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    chat_type: str = "solo"
    mode: str = "|"
    agents: list[str] = Field(default_factory=list)
    strictness: int = 50
    pass_threshold: int = 70
    max_iterations: int = 10
    rubric: Optional[dict[str, Any]] = None


class SendMessageRequest(BaseModel):
    from_id: str = "user"
    content: str
    message_type: str = "user"
    to_id: Optional[str] = None
    mentions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrictnessRequest(BaseModel):
    strictness: int


class LockAcquireRequest(BaseModel):
    file_path: str
    agent_id: str
    reason: Optional[str] = None


class LockReleaseRequest(BaseModel):
    file_path: str
    agent_id: str


class FetchMessagesRequest(BaseModel):
    agent_id: str
    since: Optional[datetime] = None


class CreateTopicRequest(BaseModel):
    title: str
    created_by: str


# ---------------------------------------------------------------------------
# Connection endpoints
# ---------------------------------------------------------------------------

@app.post("/connect")
async def connect(req: ConnectRequest) -> dict[str, Any]:
    """Validate an agent connection."""
    from clawlink_router.models import AuthMethod
    try:
        method = AuthMethod(req.auth_method)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown auth method: {req.auth_method}")

    config = ConnectionConfig(
        endpoint=req.endpoint,
        auth_method=method,
        credentials=req.credentials,
        pairing_code=req.pairing_code,
    )
    valid = auth_manager.validate_connection(config)
    if not valid:
        raise HTTPException(status_code=401, detail="Authentication failed")
    return {"status": "connected", "endpoint": req.endpoint}


@app.post("/pair/generate")
async def pair_generate(req: PairGenerateRequest) -> dict[str, Any]:
    """Generate a new pairing code."""
    code = auth_manager.pairing_service.generate_pairing_code(
        agent_endpoint=req.agent_endpoint,
        agent_id=req.agent_id,
        metadata=req.metadata,
    )
    return code.model_dump(mode="json")


@app.post("/pair/validate")
async def pair_validate(req: PairValidateRequest) -> dict[str, Any]:
    """Validate an existing pairing code."""
    result = auth_manager.pairing_service.validate_pairing_code(req.code)
    if result is None:
        raise HTTPException(status_code=404, detail="Invalid or expired pairing code")
    return result.model_dump(mode="json")


@app.post("/pair/complete")
async def pair_complete(req: PairCompleteRequest) -> dict[str, Any]:
    """Complete the pairing flow and register the agent."""
    agent = AgentInfo(
        agent_id=req.agent_id,
        display_name=req.display_name,
        agent_type=req.agent_type,
        endpoint=req.endpoint,
        avatar_color=req.avatar_color,
        metadata=req.metadata,
    )
    try:
        result = auth_manager.pairing_service.complete_pairing(req.code, agent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    agent_registry.register(result)
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------

@app.get("/agents")
async def list_agents() -> list[dict[str, Any]]:
    """List all connected agents."""
    return [a.model_dump(mode="json") for a in agent_registry.list_all()]


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    """Get info for a specific agent."""
    agent = agent_registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump(mode="json")


@app.delete("/agents/{agent_id}")
async def disconnect_agent(agent_id: str) -> dict[str, Any]:
    """Disconnect (unregister) an agent."""
    file_lock_manager.release_all_for_agent(agent_id)
    removed = agent_registry.unregister(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "disconnected", "agent_id": agent_id}


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@app.post("/sessions")
async def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    """Create a new solo or group session."""
    from clawlink_router.models import ConversationMode
    try:
        chat_type = ChatType(req.chat_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid chat_type: {req.chat_type}")
    try:
        mode = ConversationMode(req.mode)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {req.mode}")

    agents: list[AgentInfo] = []
    for aid in req.agents:
        agent = agent_registry.get(aid)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent {aid} not found")
        agents.append(agent)

    config = SessionConfig(
        chat_type=chat_type,
        mode=mode,
        agents=req.agents,
        strictness=req.strictness,
        pass_threshold=req.pass_threshold,
        max_iterations=req.max_iterations,
        rubric=req.rubric,
    )
    state = session_manager.create(config, agents)
    return state.model_dump(mode="json")


@app.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    """List all sessions."""
    return [s.model_dump(mode="json") for s in session_manager.list_all()]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """Get full session state."""
    session = session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump(mode="json")


@app.post("/sessions/{session_id}/message")
async def send_message(session_id: str, req: SendMessageRequest) -> dict[str, Any]:
    """Send a message into a session.

    If the session is in an active teaching loop (ACTIVE/SCORING), user
    messages are queued.  In GROUP sessions, @mention routing is applied.
    """
    session = session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        msg_type = MessageType(req.message_type)
    except ValueError:
        msg_type = MessageType.USER

    msg = Message(
        session_id=session_id,
        from_id=req.from_id,
        to_id=req.to_id,
        content=req.content,
        message_type=msg_type,
        mentions=req.mentions,
        metadata=req.metadata,
    )

    # Queue user messages when agents are busy
    if (
        req.from_id == "user"
        and session.status in (SessionStatus.ACTIVE, SessionStatus.SCORING)
    ):
        queued = message_queue.enqueue(session_id, msg)
        await _ws_broadcast(session_id, {"event": "message_queued", "message_id": msg.id, "position": queued.position})
        return {"status": "queued", "position": queued.position, "message_id": msg.id}

    # Group chat routing
    if session.chat_type == ChatType.GROUP:
        targets = await conversation_router.route_group_message(session_id, msg)
        await _ws_broadcast(session_id, {"event": "new_message", "message_id": msg.id, "targets": targets})
        return {"status": "routed", "targets": targets, "message_id": msg.id}

    # Default: just store it
    session_manager.add_message(session_id, msg)
    await _ws_broadcast(session_id, {"event": "new_message", "message_id": msg.id})
    return {"status": "delivered", "message_id": msg.id}


@app.post("/sessions/{session_id}/teach")
async def start_teaching(session_id: str) -> dict[str, Any]:
    """Start the bilateral teaching loop for a solo session (runs async)."""
    session = session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.chat_type != ChatType.SOLO:
        raise HTTPException(status_code=400, detail="Teaching loop requires a SOLO session")
    if len(session.agents) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 agents")

    async def _run() -> None:
        try:
            scores = await conversation_router.run_teaching_loop(session_id)
            await _ws_broadcast(session_id, {
                "event": "teaching_complete",
                "scores": [s.model_dump(mode="json") for s in scores],
            })
        except Exception as exc:
            logger.exception("Teaching loop failed for session %s", session_id)
            await _ws_broadcast(session_id, {
                "event": "teaching_error",
                "error": str(exc),
            })

    asyncio.ensure_future(_run())
    return {"status": "teaching_started", "session_id": session_id}


@app.put("/sessions/{session_id}/strictness")
async def update_strictness(session_id: str, req: StrictnessRequest) -> dict[str, Any]:
    """Update session strictness (0-100)."""
    ok = session_manager.update_strictness(session_id, req.strictness)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "strictness": req.strictness}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """Delete / clean up a session."""
    ok = conversation_router.cleanup_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


# ---------------------------------------------------------------------------
# Message Queue endpoints
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/queue")
async def view_queue(session_id: str) -> dict[str, Any]:
    """View the message queue for a session."""
    items = message_queue.peek(session_id)
    return {
        "session_id": session_id,
        "length": message_queue.get_queue_length(session_id),
        "items": [q.model_dump(mode="json") for q in items],
    }


@app.post("/sessions/{session_id}/queue/flush")
async def flush_queue(session_id: str) -> dict[str, Any]:
    """Force-flush the message queue, delivering all queued messages."""
    session = session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = message_queue.process_queue(session_id)
    for msg in messages:
        session_manager.add_message(session_id, msg)
    await _ws_broadcast(session_id, {"event": "queue_flushed", "count": len(messages)})
    return {
        "status": "flushed",
        "delivered": len(messages),
        "message_ids": [m.id for m in messages],
    }


# ---------------------------------------------------------------------------
# Group Chat endpoints
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/topics")
async def create_topic(session_id: str, req: CreateTopicRequest) -> dict[str, Any]:
    """Create a new group chat topic."""
    session = session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    topic = group_chat_manager.create_topic(session_id, req.title, req.created_by)
    return topic.model_dump(mode="json")


@app.get("/sessions/{session_id}/topics")
async def list_topics(session_id: str) -> list[dict[str, Any]]:
    """List all topics in a group session."""
    return [t.model_dump(mode="json") for t in group_chat_manager.list_topics(session_id)]


@app.get("/sessions/{session_id}/topics/{topic_id}/messages")
async def get_topic_messages(session_id: str, topic_id: str) -> list[dict[str, Any]]:
    """Get messages in a specific topic."""
    msgs = group_chat_manager.get_topic_messages(topic_id)
    return [m.model_dump(mode="json") for m in msgs]


@app.post("/sessions/{session_id}/fetch-messages")
async def fetch_messages(session_id: str, req: FetchMessagesRequest) -> list[dict[str, Any]]:
    """Agent fetches all group messages (optionally since a timestamp)."""
    msgs = group_chat_manager.fetch_messages(session_id, req.agent_id, req.since)
    return [m.model_dump(mode="json") for m in msgs]


# ---------------------------------------------------------------------------
# File Lock endpoints
# ---------------------------------------------------------------------------

@app.post("/locks/acquire")
async def acquire_lock(req: LockAcquireRequest) -> dict[str, Any]:
    """Acquire a file lock."""
    acquired = file_lock_manager.acquire(req.file_path, req.agent_id, req.reason)
    if not acquired:
        status = file_lock_manager.check(req.file_path)
        return {
            "acquired": False,
            "locked_by": status.locked_by,
            "wait_queue": status.wait_queue,
        }
    return {"acquired": True, "file_path": req.file_path, "agent_id": req.agent_id}


@app.post("/locks/release")
async def release_lock(req: LockReleaseRequest) -> dict[str, Any]:
    """Release a file lock."""
    released = file_lock_manager.release(req.file_path, req.agent_id)
    if not released:
        raise HTTPException(status_code=400, detail="Cannot release lock (not held or wrong agent)")
    return {"released": True, "file_path": req.file_path}


@app.get("/locks")
async def list_locks() -> list[dict[str, Any]]:
    """List all active file locks."""
    return [l.model_dump(mode="json") for l in file_lock_manager.list_locks()]


@app.get("/locks/{file_path:path}")
async def check_lock(file_path: str) -> dict[str, Any]:
    """Check lock status for a specific file path."""
    status = file_lock_manager.check(file_path)
    return status.model_dump(mode="json")


@app.delete("/locks/{file_path:path}")
async def force_release_lock(file_path: str) -> dict[str, Any]:
    """Force-release a lock regardless of holder."""
    released = file_lock_manager.force_release(file_path)
    if not released:
        raise HTTPException(status_code=404, detail="No lock found on path")
    return {"force_released": True, "file_path": file_path}


# ---------------------------------------------------------------------------
# Monitoring endpoints
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/heartbeat")
async def get_heartbeat(session_id: str) -> dict[str, Any]:
    """Get per-agent heartbeat status for a session."""
    hb = session_manager.get_heartbeat(session_id)
    if not hb:
        raise HTTPException(status_code=404, detail="Session not found or no agents")
    return {"session_id": session_id, "heartbeat": hb}


@app.get("/health")
async def health() -> dict[str, Any]:
    """Router health check."""
    return {
        "status": "healthy",
        "version": __version__,
        "agents_connected": len(agent_registry),
        "sessions_active": len(session_manager.list_all()),
    }


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    """Aggregated teaching metrics."""
    sessions = session_manager.list_all()
    total = len(sessions)
    all_scores = []
    challenge_count = 0
    for s in sessions:
        for sc in s.scores:
            all_scores.append(sc.score)
            if sc.student_challenge_accepted:
                challenge_count += 1

    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    avg_convergence = 0.0
    if total > 0:
        iterations = [s.current_iteration for s in sessions if s.current_iteration > 0]
        avg_convergence = sum(iterations) / len(iterations) if iterations else 0.0

    error_count = sum(1 for s in sessions if s.status == SessionStatus.FAILED)
    error_rate = error_count / total if total > 0 else 0.0
    challenge_rate = challenge_count / len(all_scores) if all_scores else 0.0

    m = TeachingMetrics(
        total_sessions=total,
        avg_convergence=round(avg_convergence, 2),
        error_rate=round(error_rate, 4),
        avg_score=round(avg_score, 2),
        challenge_rate=round(challenge_rate, 4),
    )
    return m.model_dump(mode="json")


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """Real-time updates for a session (messages, queue, agent status, scores)."""
    await websocket.accept()
    _ws_connections.setdefault(session_id, []).append(websocket)
    logger.info("WebSocket connected for session %s", session_id)
    try:
        while True:
            data = await websocket.receive_json()
            # Echo-back / keep-alive support
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "message":
                # Client can send messages via WS too
                msg = Message(
                    session_id=session_id,
                    from_id=data.get("from_id", "user"),
                    content=data.get("content", ""),
                    message_type=MessageType.USER,
                    mentions=data.get("mentions", []),
                    metadata=data.get("metadata", {}),
                )
                session = session_manager.get(session_id)
                if session and session.status in (SessionStatus.ACTIVE, SessionStatus.SCORING):
                    queued = message_queue.enqueue(session_id, msg)
                    await _ws_broadcast(session_id, {
                        "event": "message_queued",
                        "message_id": msg.id,
                        "position": queued.position,
                    })
                else:
                    session_manager.add_message(session_id, msg)
                    await _ws_broadcast(session_id, {
                        "event": "new_message",
                        "message_id": msg.id,
                    })
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except Exception:
        logger.exception("WebSocket error for session %s", session_id)
    finally:
        conns = _ws_connections.get(session_id, [])
        if websocket in conns:
            conns.remove(websocket)
