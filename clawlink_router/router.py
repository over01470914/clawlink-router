"""ClawLink Router - Conversation router (solo teaching loop + group chat)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from clawlink_router.agents import AgentRegistry
from clawlink_router.ai_client import AIClient
from clawlink_router.group_chat import GroupChatManager
from clawlink_router.message_queue import MessageQueue
from clawlink_router.models import (
    ChatType,
    ConversationMode,
    Message,
    MessageType,
    ScoreResult,
    SelfAssessment,
    SessionState,
    SessionStatus,
)
from clawlink_router.scoring import ScoringEngine
from clawlink_router.session import SessionManager

logger = logging.getLogger(__name__)


class ConversationRouter:
    """Orchestrates solo bilateral teaching loops and group chat routing.

    Solo mode 7-step bilateral teaching loop:
        1. Teacher sends a teaching message to student.
        2. Student processes and responds.
        3. Teacher sends a challenge.
        4. Student responds to the challenge.
        5. Student submits self-assessment.
        6. Teacher provides feedback (score).
        7. Router computes the final blended score.

    After each exchange cycle the router checks the message queue for
    pending user messages and delivers them before continuing.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        agent_registry: AgentRegistry,
        ai_client: AIClient,
        message_queue: MessageQueue,
        group_chat: GroupChatManager,
        scoring_engine: Optional[ScoringEngine] = None,
    ) -> None:
        self._sessions = session_manager
        self._registry = agent_registry
        self._client = ai_client
        self._queue = message_queue
        self._group = group_chat
        self._scoring = scoring_engine or ScoringEngine()

    # ------------------------------------------------------------------
    # Solo teaching loop
    # ------------------------------------------------------------------

    async def run_teaching_loop(self, session_id: str) -> list[ScoreResult]:
        """Execute the full bilateral teaching loop for a SOLO session.

        Returns the list of ScoreResults (one per iteration that reached scoring).
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        if session.chat_type != ChatType.SOLO:
            raise ValueError("Teaching loop is only supported for SOLO sessions")
        if len(session.agents) < 2:
            raise ValueError("Teaching loop requires at least 2 agents")

        self._sessions.set_status(session_id, SessionStatus.ACTIVE)

        teacher = session.agents[0]
        student = session.agents[1]

        if session.mode == ConversationMode.B_LEADS:
            teacher, student = student, teacher

        scores: list[ScoreResult] = []
        max_iter = session.max_iterations

        for iteration in range(1, max_iter + 1):
            self._sessions.increment_iteration(session_id)
            logger.info(
                "Session %s: iteration %d/%d", session_id, iteration, max_iter
            )

            try:
                score = await self._run_single_iteration(
                    session=session,
                    teacher=teacher,
                    student=student,
                    iteration=iteration,
                )
                scores.append(score)
                session.scores.append(score)

                # Deliver any queued user messages between iterations
                await self._flush_user_queue(session)

                if score.passed:
                    logger.info(
                        "Session %s passed on iteration %d (%.1f)",
                        session_id,
                        iteration,
                        score.score,
                    )
                    break
            except Exception:
                logger.exception(
                    "Session %s: error in iteration %d", session_id, iteration
                )
                self._sessions.set_status(session_id, SessionStatus.FAILED)
                raise

        final_status = (
            SessionStatus.COMPLETED if scores and scores[-1].passed
            else SessionStatus.COMPLETED
        )
        self._sessions.set_status(session_id, final_status)
        return scores

    async def _run_single_iteration(
        self,
        session: SessionState,
        teacher: Any,
        student: Any,
        iteration: int,
    ) -> ScoreResult:
        """Execute one 7-step teaching cycle."""
        sid = session.session_id

        # Step 1 - Teacher sends teaching message
        teaching_prompt = (
            f"[Iteration {iteration}] Please teach the student about the current topic. "
            f"Strictness level: {session.strictness}/100."
        )
        teacher_msg = await self._client.send_message(
            teacher.endpoint, teaching_prompt, session_id=sid,
            sender_id="router",
            metadata={"message_type": "teaching", "role": "teacher", "capture_memory": True},
        )
        self._record(sid, teacher.agent_id, student.agent_id, teacher_msg, MessageType.TEACHING)

        # Step 2 - Student processes and responds
        student_resp = await self._client.send_message(
            student.endpoint, teacher_msg, session_id=sid,
            sender_id=teacher.agent_id,
            metadata={"message_type": "teaching", "role": "student", "capture_memory": True},
        )
        self._record(sid, student.agent_id, teacher.agent_id, student_resp, MessageType.RESPONSE)

        # Step 3 - Teacher sends a challenge
        challenge_prompt = (
            f"Based on the student's response, provide a challenge question. "
            f"Student said: {student_resp[:500]}"
        )
        challenge_msg = await self._client.send_message(
            teacher.endpoint, challenge_prompt, session_id=sid,
            sender_id="router",
            metadata={"message_type": "challenge", "role": "teacher", "capture_memory": True},
        )
        self._record(sid, teacher.agent_id, student.agent_id, challenge_msg, MessageType.CHALLENGE)

        # Step 4 - Student responds to challenge
        challenge_resp = await self._client.send_message(
            student.endpoint, challenge_msg, session_id=sid,
            sender_id=teacher.agent_id,
            metadata={"message_type": "challenge", "role": "student", "capture_memory": True},
        )
        self._record(sid, student.agent_id, teacher.agent_id, challenge_resp, MessageType.RESPONSE)

        # Step 5 - Student self-assessment
        assess_prompt = (
            "Provide a self-assessment of your understanding. "
            "Rate your confidence from 0.0 to 1.0 and explain your reasoning."
        )
        assess_resp = await self._client.send_message(
            student.endpoint, assess_prompt, session_id=sid,
            sender_id="router",
            metadata={"message_type": "self_assessment", "role": "student"},
        )
        self._record(
            sid, student.agent_id, teacher.agent_id, assess_resp, MessageType.SELF_ASSESSMENT
        )
        self_assessment = self._parse_self_assessment(student.agent_id, assess_resp)

        # Step 6 - Teacher feedback / score
        feedback_prompt = (
            f"Score the student's overall performance (0-100). "
            f"Student's self-assessment: confidence={self_assessment.confidence:.2f}, "
            f"reasoning={self_assessment.reasoning[:300]}. "
            f"Challenge response: {challenge_resp[:300]}"
        )
        feedback_resp = await self._client.send_message(
            teacher.endpoint, feedback_prompt, session_id=sid,
            sender_id="router",
            metadata={"message_type": "feedback", "role": "teacher"},
        )
        self._record(sid, teacher.agent_id, student.agent_id, feedback_resp, MessageType.FEEDBACK)
        teacher_score = self._parse_teacher_score(feedback_resp)

        # Step 7 - Router computes final blended score
        self._sessions.set_status(sid, SessionStatus.SCORING)
        result = self._scoring.score(
            strictness=session.strictness,
            teacher_score=teacher_score,
            self_assessment=self_assessment,
            pass_threshold=session.pass_threshold,
            rubric=session.rubric,
            teacher_listened="listened" in feedback_resp.lower()
                or "incorporated" in feedback_resp.lower(),
            student_challenge_accepted="accept" in challenge_resp.lower()
                or "agree" in challenge_resp.lower(),
        )
        self._sessions.set_status(sid, SessionStatus.ACTIVE)
        return result

    async def _flush_user_queue(self, session: SessionState) -> None:
        """Deliver any queued user messages to all agents in the session."""
        messages = self._queue.process_queue(session.session_id)
        for msg in messages:
            for agent in session.agents:
                try:
                    resp = await self._client.send_message(
                        agent.endpoint,
                        f"[User message] {msg.content}",
                        session_id=session.session_id,
                    )
                    self._record(
                        session.session_id,
                        agent.agent_id,
                        "user",
                        resp,
                        MessageType.USER,
                    )
                except Exception:
                    logger.warning(
                        "Failed to deliver queued message to agent %s",
                        agent.agent_id,
                    )

    # ------------------------------------------------------------------
    # Group chat routing
    # ------------------------------------------------------------------

    async def route_group_message(self, session_id: str, message: Message) -> list[str]:
        """Route a message in a GROUP session using @mention logic.

        Returns the list of agent_ids the message was actively delivered to.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        self._sessions.add_message(session_id, message)
        targets = self._group.route_message(session_id, message)

        # Actually push the message to targeted agents
        for agent_id in targets:
            agent = self._registry.get(agent_id)
            if agent is None:
                logger.warning("Mentioned agent %s not found in registry", agent_id)
                continue
            try:
                await self._client.send_message(
                    agent.endpoint,
                    f"[Group @mention from {message.from_id}] {message.content}",
                    session_id=session_id,
                )
            except Exception:
                logger.warning(
                    "Failed to deliver group message to agent %s", agent_id
                )
        return targets

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_session(self, session_id: str) -> bool:
        """Clean up all resources associated with a session."""
        self._queue.clear(session_id)
        deleted = self._sessions.delete(session_id)
        if deleted:
            logger.info("Cleaned up session %s", session_id)
        return deleted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record(
        self,
        session_id: str,
        from_id: str,
        to_id: str,
        content: str,
        msg_type: MessageType,
    ) -> None:
        """Persist a message in the session."""
        msg = Message(
            session_id=session_id,
            from_id=from_id,
            to_id=to_id,
            content=content,
            message_type=msg_type,
        )
        self._sessions.add_message(session_id, msg)

    @staticmethod
    def _parse_self_assessment(agent_id: str, text: str) -> SelfAssessment:
        """Best-effort parse of a self-assessment from free text."""
        confidence = 0.5
        # Try to extract a float after "confidence"
        import re

        match = re.search(r"confidence[:\s]*([01]?\.\d+)", text, re.IGNORECASE)
        if match:
            try:
                confidence = float(match.group(1))
                confidence = max(0.0, min(1.0, confidence))
            except ValueError:
                confidence = 0.5

        return SelfAssessment(
            agent_id=agent_id,
            confidence=confidence,
            rubric_scores={},
            reasoning=text[:1000],
        )

    @staticmethod
    def _parse_teacher_score(text: str) -> float:
        """Best-effort parse of a numeric score from teacher feedback."""
        import re

        match = re.search(r"(\d{1,3})\s*/?\s*100", text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return 50.0
        # Fallback: look for any number in [0, 100]
        numbers = re.findall(r"\b(\d{1,3})\b", text)
        for n in numbers:
            val = int(n)
            if 0 <= val <= 100:
                return float(val)
        return 50.0
