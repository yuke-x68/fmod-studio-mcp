"""Tool registration discovery for FMOD Studio MCP Server.

Each tool module exposes a ``register(mcp_server, get_client)`` function.
``register_all_tools`` discovers and invokes them automatically, so
``server.py`` does not need to know about individual tool modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from fmod_studio_mcp.tools import execute_script

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from fmod_studio_mcp.bridge import BridgeConfig, FmodTcpClient

_TOOL_MODULES = [
    execute_script,
]


def register_all_tools(
    mcp_server: MCPServer,
    get_client: Callable[[], tuple[FmodTcpClient, BridgeConfig]],
) -> None:
    """Discover and register all tool modules with the MCP server.

    Args:
        mcp_server: The MCPServer instance.
        get_client: Factory function returning (FmodTcpClient, BridgeConfig).
    """
    for module in _TOOL_MODULES:
        module.register(mcp_server, get_client)
