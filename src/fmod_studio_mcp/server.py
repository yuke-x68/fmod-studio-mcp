"""MCPServer entry point — tool registration and wiring (Section 2.2)."""

from __future__ import annotations

import argparse
import logging
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from mcp.server.mcpserver import MCPServer

from fmod_studio_mcp.bridge import BridgeConfig, FmodTcpClient
from fmod_studio_mcp.tools import register_all_tools

logger = logging.getLogger(__name__)


def _get_version() -> str:
    """Return the installed package version, or "unknown" if not installed."""
    try:
        return _pkg_version("fmod-studio-mcp")
    except PackageNotFoundError:
        return "unknown"


# mcp 2.x reports an empty serverInfo.version unless one is passed explicitly.
mcp = MCPServer("fmod-studio", version=_get_version())

# Shared instances (lazy-initialized on first tool call)
_config: BridgeConfig | None = None
_client: FmodTcpClient | None = None


def _get_client() -> tuple[FmodTcpClient, BridgeConfig]:
    """Lazy-initialize TCP client and config."""
    global _config, _client
    if _config is None:
        _config = BridgeConfig()
        _client = FmodTcpClient(_config)
        logger.info(
            "FMOD TCP client initialized (host=%s, port=%d)",
            _config.host,
            _config.port,
        )
    assert _client is not None
    return _client, _config


# Register all tools via self-registration pattern (V2+V3 resolution)
register_all_tools(mcp, _get_client)


def _configure_logging(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, configure logging, and print startup info to stderr.

    Separated from main() for testability.
    Returns the effective log level (e.g. logging.WARNING).
    """
    parser = argparse.ArgumentParser(
        prog="fmod-studio-mcp",
        description="FMOD Studio MCP Server — controls FMOD Studio via TCP:3663",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Set log level to DEBUG",
    )
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log level (DEBUG/INFO/WARNING/ERROR/CRITICAL). Overrides --verbose. Default: WARNING",
    )
    args = parser.parse_args(argv)

    # --log-level takes priority over --verbose
    if args.log_level is not None:
        level = getattr(logging, args.log_level)
    elif args.verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    # Print startup info to stderr (stdout is reserved for MCP JSON-RPC protocol)
    cfg = BridgeConfig()
    sys.stderr.write(f"FMOD Studio MCP Server v{_get_version()}\n")
    sys.stderr.write(f"Connecting to FMOD Studio at {cfg.host}:{cfg.port}\n")
    sys.stderr.flush()

    return level


def main() -> None:
    """Entry point for the fmod-studio-mcp command."""
    _configure_logging()
    mcp.run()


if __name__ == "__main__":
    main()
