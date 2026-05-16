"""
MCP server abstraction.

An MCPServer wraps an external API (Salesforce, Gmail, etc.) behind a
uniform interface that the sandbox can call via HTTP. Each server
declares its tools (functions) and their parameter schemas.

The key invariant: the SANDBOX never holds credentials. It calls
the backend's /mcp endpoint, which fetches credentials from the vault
and invokes the MCPServer here. Tokens never leave the backend process.

Adding a new MCP server:
  1. Subclass MCPServer
  2. Register it in registry.py
  3. Done — sandbox can immediately use it via /mcp/{name}/{tool}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class MCPError(Exception):
    """Generic MCP failure. Subclasses below for specific cases."""


class MCPAuthError(MCPError):
    """Credentials missing, expired, or invalid for this provider."""


class MCPToolNotFound(MCPError):
    """Requested tool name doesn't exist on this server."""


class MCPInvalidArgs(MCPError):
    """Tool arguments failed validation."""


class ToolSchema(BaseModel):
    """Describes one callable tool. Used both for /mcp introspection
    endpoints and for plan_generator (Phase 2a.2) so the LLM knows
    what tools exist and how to call them."""
    name: str
    description: str
    # JSON Schema for the args dict. Keep it simple — top-level object
    # with named properties. Used for validation and for prompting the
    # plan generator.
    args_schema: dict[str, Any] = Field(default_factory=dict)
    # Free-form examples to help the LLM understand usage.
    examples: list[dict[str, Any]] = Field(default_factory=list)


class MCPServer(ABC):
    """One server = one external service wrapped as callable tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Canonical lowercase identifier ('salesforce', 'gmail', 'mock')."""

    @property
    @abstractmethod
    def credential_provider(self) -> str | None:
        """Which vault credential to fetch for calls to this server.
        Returns None if the server needs no auth (e.g. mock)."""

    @abstractmethod
    async def list_tools(self) -> list[ToolSchema]:
        """Return the set of tools this server exposes."""

    @abstractmethod
    async def call_tool(
        self,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Execute one tool call.

        Args:
            tool: tool name (one of those returned by list_tools)
            args: validated arguments for the tool
            credentials: decrypted credential dict from the vault,
                or None if credential_provider is None.

        Returns:
            A JSON-serializable dict with the tool's output.

        Raises:
            MCPToolNotFound if tool isn't recognized
            MCPInvalidArgs if args are malformed
            MCPAuthError if credentials are missing/expired
            MCPError for other failures (API errors, network, etc.)
        """