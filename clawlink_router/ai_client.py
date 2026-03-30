"""ClawLink Router - AI client protocol and implementations."""

from __future__ import annotations

import abc
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class AIClient(abc.ABC):
    """Protocol that every agent communication backend must implement."""

    @abc.abstractmethod
    async def send_message(
        self,
        endpoint: str,
        message: str,
        *,
        session_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        sender_id: Optional[str] = None,
    ) -> str:
        """Send a message to an agent and return its response text."""
        ...

    @abc.abstractmethod
    async def ping(self, endpoint: str) -> bool:
        """Health-check an agent. Returns True if reachable."""
        ...

    @abc.abstractmethod
    async def memory_command(
        self,
        endpoint: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Issue a memory-related command (save, recall, forget, etc.)."""
        ...


class GenericAIClient(AIClient):
    """HTTP-based client that can talk to any agent with a REST endpoint.

    Expected agent API surface:
        POST /message  -> { "response": "..." }
        GET  /health   -> 200
        POST /memory   -> { ... }
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def send_message(
        self,
        endpoint: str,
        message: str,
        *,
        session_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        sender_id: Optional[str] = None,
    ) -> str:
        """POST a message to the agent's ``/message`` endpoint."""
        url = endpoint.rstrip("/") + "/message"
        body: dict[str, Any] = {"content": message}
        if session_id:
            body["session_id"] = session_id
        if sender_id:
            body["sender_id"] = sender_id
        if metadata:
            body["metadata"] = metadata
        else:
            body["metadata"] = {}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
                response_text: str = data.get("response", data.get("content", ""))
                logger.debug("Agent at %s responded: %s", endpoint, response_text[:120])
                return response_text
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Agent %s returned HTTP %d: %s",
                    endpoint,
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                raise
            except httpx.RequestError as exc:
                logger.error("Failed to reach agent at %s: %s", endpoint, exc)
                raise

    async def ping(self, endpoint: str) -> bool:
        """GET the agent's ``/health`` endpoint."""
        url = endpoint.rstrip("/") + "/health"
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.get(url)
                return resp.status_code == 200
            except httpx.RequestError:
                return False

    async def memory_command(
        self,
        endpoint: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """POST a memory command to the agent's ``/memory`` endpoint."""
        url = endpoint.rstrip("/") + "/memory"
        body = {"command": command, **payload}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                result: dict[str, Any] = resp.json()
                return result
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Memory command failed at %s (HTTP %d): %s",
                    endpoint,
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                raise
            except httpx.RequestError as exc:
                logger.error("Memory command unreachable at %s: %s", endpoint, exc)
                raise


class MockAIClient(AIClient):
    """In-memory mock for testing without real agent endpoints."""

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.memory_commands: list[dict[str, Any]] = []
        self._response_queue: list[str] = []
        self._default_response: str = "Mock response"

    def set_responses(self, responses: list[str]) -> None:
        """Pre-load a queue of responses that will be returned in order."""
        self._response_queue = list(responses)

    def set_default_response(self, text: str) -> None:
        """Set the fallback response when the queue is empty."""
        self._default_response = text

    async def send_message(
        self,
        endpoint: str,
        message: str,
        *,
        session_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        sender_id: Optional[str] = None,
    ) -> str:
        self.sent_messages.append(
            {
                "endpoint": endpoint,
                "message": message,
                "session_id": session_id,
                "metadata": metadata,
                "sender_id": sender_id,
            }
        )
        if self._response_queue:
            return self._response_queue.pop(0)
        return self._default_response

    async def ping(self, endpoint: str) -> bool:
        return True

    async def memory_command(
        self,
        endpoint: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        record = {"endpoint": endpoint, "command": command, "payload": payload}
        self.memory_commands.append(record)
        return {"status": "ok", "command": command}
