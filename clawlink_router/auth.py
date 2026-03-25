"""ClawLink Router - Authentication and pairing-code management."""

from __future__ import annotations

import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from clawlink_router.models import (
    AgentInfo,
    AuthMethod,
    ConnectionConfig,
    PairingCode,
)

logger = logging.getLogger(__name__)

PAIRING_CODE_TTL_MINUTES = 10


def _generate_code_string() -> str:
    """Return a random XXXX-XXXX alphanumeric code."""
    part = lambda: "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{part()}-{part()}"


class PairingService:
    """Generate, validate, and complete pairing-code flows."""

    def __init__(self) -> None:
        self._codes: dict[str, PairingCode] = {}

    def generate_pairing_code(
        self,
        agent_endpoint: str,
        agent_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PairingCode:
        """Create a new pairing code that expires after *PAIRING_CODE_TTL_MINUTES*."""
        now = datetime.now(timezone.utc)
        code = _generate_code_string()
        while code in self._codes:
            code = _generate_code_string()

        pairing = PairingCode(
            code=code,
            agent_endpoint=agent_endpoint,
            agent_id=agent_id,
            created_at=now,
            expires_at=now + timedelta(minutes=PAIRING_CODE_TTL_MINUTES),
            metadata=metadata or {},
        )
        self._codes[code] = pairing
        logger.info("Generated pairing code %s for agent %s", code, agent_id)
        return pairing

    def validate_pairing_code(self, code: str) -> Optional[PairingCode]:
        """Return the PairingCode if it exists and has not expired, else None."""
        pairing = self._codes.get(code)
        if pairing is None:
            logger.debug("Pairing code %s not found", code)
            return None
        if datetime.now(timezone.utc) > pairing.expires_at:
            logger.info("Pairing code %s has expired", code)
            del self._codes[code]
            return None
        return pairing

    def complete_pairing(self, code: str, agent_info: AgentInfo) -> AgentInfo:
        """Consume the pairing code and return the finalised AgentInfo.

        Raises ValueError if the code is invalid or expired.
        """
        pairing = self.validate_pairing_code(code)
        if pairing is None:
            raise ValueError(f"Invalid or expired pairing code: {code}")
        del self._codes[code]
        logger.info(
            "Pairing complete for code %s -> agent %s", code, agent_info.agent_id
        )
        return agent_info

    def cleanup_expired(self) -> int:
        """Remove all expired codes and return how many were purged."""
        now = datetime.now(timezone.utc)
        expired = [c for c, p in self._codes.items() if now > p.expires_at]
        for c in expired:
            del self._codes[c]
        if expired:
            logger.info("Purged %d expired pairing codes", len(expired))
        return len(expired)


class AuthManager:
    """Validate agent connections across all supported auth methods."""

    def __init__(self) -> None:
        self.pairing_service = PairingService()
        self._api_keys: dict[str, str] = {}  # key -> agent_id

    def register_api_key(self, key: str, agent_id: str) -> None:
        """Pre-register a valid API key for an agent."""
        self._api_keys[key] = agent_id
        logger.info("Registered API key for agent %s", agent_id)

    def validate_connection(self, config: ConnectionConfig) -> bool:
        """Validate an inbound connection attempt.

        Returns True when the credentials are acceptable for the declared
        auth method; False otherwise.
        """
        method = config.auth_method
        creds = config.credentials

        if method == AuthMethod.PAIRING_CODE:
            code = config.pairing_code or creds.get("pairing_code", "")
            result = self.pairing_service.validate_pairing_code(code)
            if result is None:
                logger.warning("Pairing-code auth failed for endpoint %s", config.endpoint)
                return False
            return True

        if method == AuthMethod.API_KEY:
            key = creds.get("api_key", "")
            if key in self._api_keys:
                return True
            logger.warning("API-key auth failed for endpoint %s", config.endpoint)
            return False

        if method == AuthMethod.SSH:
            host = creds.get("host")
            username = creds.get("username")
            if not host or not username:
                logger.warning("SSH auth missing host/username for %s", config.endpoint)
                return False
            private_key = creds.get("private_key") or creds.get("private_key_path")
            password = creds.get("password")
            if not private_key and not password:
                logger.warning("SSH auth missing credentials for %s", config.endpoint)
                return False
            logger.info("SSH auth accepted (key/password present) for %s", config.endpoint)
            return True

        if method == AuthMethod.MTLS:
            cert = creds.get("client_cert")
            key = creds.get("client_key")
            if not cert or not key:
                logger.warning("mTLS auth missing cert/key for %s", config.endpoint)
                return False
            logger.info("mTLS auth accepted for %s", config.endpoint)
            return True

        if method == AuthMethod.OAUTH:
            token = creds.get("access_token") or creds.get("token")
            if not token:
                logger.warning("OAuth auth missing token for %s", config.endpoint)
                return False
            logger.info("OAuth auth accepted for %s", config.endpoint)
            return True

        logger.error("Unknown auth method: %s", method)
        return False
