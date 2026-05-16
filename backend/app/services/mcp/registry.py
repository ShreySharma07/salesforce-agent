"""
MCP server registry. ONE place that knows about all MCP servers.

To register a new server: import it here, add to _SERVERS dict.
"""
from __future__ import annotations

from app.services.mcp.base import MCPServer
from app.services.mcp.gmail import GmailMCPServer
from app.services.mcp.mock_server import MockMCPServer
from app.services.mcp.salesforce import SalesforceMCPServer


_SERVERS: dict[str, MCPServer] = {
    "mock": MockMCPServer(),
    "salesforce": SalesforceMCPServer(),
    "gmail": GmailMCPServer(),
}


def get_mcp_server(name: str) -> MCPServer:
    name = name.lower()
    server = _SERVERS.get(name)
    if server is None:
        raise KeyError(f"unknown MCP server: {name!r}. "
                       f"Available: {list(_SERVERS)}")
    return server


def list_mcp_servers() -> list[MCPServer]:
    return list(_SERVERS.values())