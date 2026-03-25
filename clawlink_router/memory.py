"""Memory file generator - persists session knowledge as markdown."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import aiofiles

from clawlink_router.models import (
    ConversationMode,
    MemoryFile,
    Message,
    SessionState,
)

logger = logging.getLogger(__name__)


def _slugify(text: str, max_length: int = 48) -> str:
    """Convert *text* to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:max_length]


def _resolve_roles(state: SessionState) -> tuple[str, str]:
    """Return ``(teacher, student)`` based on the conversation mode."""
    if len(state.agents) >= 2:
        first = state.agents[0].display_name
        second = state.agents[1].display_name
    else:
        first, second = "Agent A", "Agent B"

    if state.mode == ConversationMode.A_LEADS:
        return first, second
    if state.mode == ConversationMode.B_LEADS:
        return second, first
    return first, second


def _summarise_messages(messages: list[Message], max_lines: int = 60) -> str:
    """Build a concise conversation transcript."""
    lines: list[str] = []
    for msg in messages:
        tag = msg.from_id
        snippet = msg.content[:300].replace("\n", " ")
        lines.append(f"**{tag}**: {snippet}")
        if len(lines) >= max_lines:
            lines.append(f"_... ({len(messages) - max_lines} more messages omitted)_")
            break
    return "\n\n".join(lines)


def _extract_key_decisions(messages: list[Message]) -> list[str]:
    """Heuristically pull out key decision points from the conversation."""
    decisions: list[str] = []
    decision_markers = (
        "decided", "decision", "agreed", "conclusion", "resolved",
        "chosen", "selected", "will use", "we should", "the answer is",
    )
    for msg in messages:
        lower = msg.content.lower()
        if any(marker in lower for marker in decision_markers):
            snippet = msg.content[:200].replace("\n", " ").strip()
            decisions.append(f"[{msg.from_id}] {snippet}")
    return decisions


class MemoryGenerator:
    """Generates persistent markdown memory files from completed sessions."""

    async def generate(
        self,
        session: SessionState,
        topic: str,
        memories_dir: str,
    ) -> MemoryFile:
        """Write a memory file and return its metadata.

        Parameters
        ----------
        session:
            The session whose conversation will be persisted.
        topic:
            A human-readable topic label.
        memories_dir:
            Directory in which to write the file.

        Returns
        -------
        MemoryFile
        """
        os.makedirs(memories_dir, exist_ok=True)

        now = datetime.now(timezone.utc)
        teacher, student = _resolve_roles(session)
        key_decisions = _extract_key_decisions(session.messages)
        final_score = session.scores[-1].score if session.scores else 0

        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        filename = f"memory_{timestamp_str}_{_slugify(topic)}.md"
        filepath = os.path.join(memories_dir, filename)

        content = self._render_markdown(
            topic=topic,
            teacher=teacher,
            student=student,
            mode=session.mode,
            messages=session.messages,
            key_decisions=key_decisions,
            final_score=final_score,
            scores=session.scores,
            now=now,
        )

        async with aiofiles.open(filepath, "w", encoding="utf-8") as fh:
            await fh.write(content)

        logger.info("Memory file written to %s", filepath)

        return MemoryFile(
            path=filepath,
            topic=topic,
            teacher=teacher,
            student=student,
            final_score=final_score,
            key_decisions=key_decisions,
            created_at=now,
        )

    @staticmethod
    def _render_markdown(
        *,
        topic: str,
        teacher: str,
        student: str,
        mode: ConversationMode,
        messages: list[Message],
        key_decisions: list[str],
        final_score: int,
        scores: list[Any],
        now: datetime,
    ) -> str:
        mode_labels = {
            ConversationMode.A_LEADS: "Agent A leads (>)",
            ConversationMode.DISCUSSION: "Discussion (|)",
            ConversationMode.B_LEADS: "Agent B leads (<)",
        }

        decisions_md = "\n".join(f"- {d}" for d in key_decisions) if key_decisions else "_None recorded._"

        score_history = ""
        if scores:
            rows = "\n".join(
                f"| {i + 1} | {s.score} | {'Yes' if s.passed else 'No'} | {s.feedback[:80]} |"
                for i, s in enumerate(scores)
            )
            score_history = (
                "\n## Score History\n\n"
                "| Iteration | Score | Passed | Feedback |\n"
                "|-----------|-------|--------|----------|\n"
                f"{rows}\n"
            )

        return f"""# Memory: {topic}

> Generated by ClawLink Router on {now.strftime('%Y-%m-%d %H:%M:%S UTC')}

## Overview

| Field | Value |
|-------|-------|
| **Topic** | {topic} |
| **Teaching Goal** | Transfer knowledge via structured conversation |
| **Mode** | {mode_labels.get(mode, mode.value)} |
| **Teacher** | {teacher} |
| **Student** | {student} |
| **Final Score** | {final_score}/100 |
| **Messages** | {len(messages)} |

## Key Decisions

{decisions_md}
{score_history}
## Conversation Summary

{_summarise_messages(messages)}

---
_ClawLink Router v0.1.0_
"""
