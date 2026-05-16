"""MCP (Model Context Protocol) servers — wrap external APIs as callable tools."""
from app.services.mcp.base import (
    MCPAuthError,
    MCPError,
    MCPInvalidArgs,
    MCPServer,
    MCPToolNotFound,
    ToolSchema,
)
from app.services.mcp.registry import get_mcp_server, list_mcp_servers

__all__ = [
    "MCPServer",
    "ToolSchema",
    "MCPError",
    "MCPAuthError",
    "MCPInvalidArgs",
    "MCPToolNotFound",
    "get_mcp_server",
    "list_mcp_servers",
]