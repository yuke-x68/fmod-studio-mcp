"""TCP connection configuration for FMOD Studio Scripting Terminal."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key, str(default)))


@dataclass(frozen=True)
class BridgeConfig:
    """Configuration for TCP:3663 connection to FMOD Studio Scripting Terminal.

    All settings can be overridden via environment variables.
    FMOD Studio and the MCP server run on the same Windows host, so the
    default host is localhost.
    """

    host: str = field(default_factory=lambda: _env_str("FMOD_MCP_HOST", "localhost"))
    port: int = field(default_factory=lambda: _env_int("FMOD_MCP_PORT", 3663))
    timeout_s: float = field(default_factory=lambda: _env_float("FMOD_MCP_TIMEOUT_S", 10.0))
    connect_timeout_s: float = field(default_factory=lambda: _env_float("FMOD_MCP_CONNECT_TIMEOUT_S", 5.0))
    recv_buffer_size: int = field(default_factory=lambda: _env_int("FMOD_MCP_RECV_BUFFER", 65536))
    max_retries: int = field(default_factory=lambda: _env_int("FMOD_MCP_MAX_RETRIES", 2))
    retry_delay_s: float = field(default_factory=lambda: _env_float("FMOD_MCP_RETRY_DELAY_S", 1.0))

    def resolve_host(self) -> str:
        """Return the configured host."""
        return self.host
