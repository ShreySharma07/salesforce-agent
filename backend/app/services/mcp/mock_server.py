"""
Mock MCP server. Returns hardcoded responses keyed by tool name.

Lets you validate the entire MCP pipeline (sandbox -> backend -> server
-> response) without needing any real OAuth credentials. Critical for
development before chachu finishes Salesforce setup.

Mock tools cover common patterns:
  - create_record:  takes args, echoes them back with a fake ID
  - query:          returns a fixed list of fake records
  - send_message:   logs the message, returns success
  - get_record:     returns one fake record

Use in a Plan with:
  {"kind": "mcp_call", "details": {"server": "mock",
                                    "tool": "create_record", "args": {...}}}
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.services.mcp.base import (
    MCPInvalidArgs,
    MCPServer,
    MCPToolNotFound,
    ToolSchema,
)


class MockMCPServer(MCPServer):
    @property
    def name(self) -> str:
        return "mock"

    @property
    def credential_provider(self) -> str | None:
        return None  # no auth needed

    async def list_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="create_record",
                description="Create a fake record. Echoes args back with a synthetic ID.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "Record type (Lead, Contact, etc.)"},
                        "fields": {"type": "object", "description": "Field values"},
                    },
                    "required": ["type"],
                },
                examples=[{"type": "Lead", "fields": {"FirstName": "Bob", "Company": "Acme"}}],
            ),
            ToolSchema(
                name="query",
                description="Run a fake query and return canned records.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                },
                examples=[{"query": "SELECT Id, Name FROM Account LIMIT 5"}],
            ),
            ToolSchema(
                name="get_record",
                description="Return one fake record by ID.",
                args_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                examples=[{"id": "00Q1a000FAKE001"}],
            ),
            ToolSchema(
                name="send_message",
                description="Log a fake outbound message. Returns success.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "body"],
                },
            ),
        ]

    async def call_tool(
        self,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if tool == "create_record":
            if "type" not in args:
                raise MCPInvalidArgs("create_record requires 'type'")
            return {
                "id": f"MOCK{uuid.uuid4().hex[:12].upper()}",
                "type": args["type"],
                "fields": args.get("fields", {}),
                "created_at": int(time.time()),
                "success": True,
            }
        if tool == "query":
            return {
                "records": [
                    {"id": "MOCK001", "name": "Acme Corp", "industry": "Tech"},
                    {"id": "MOCK002", "name": "Globex Inc", "industry": "Finance"},
                    {"id": "MOCK003", "name": "Initech", "industry": "Software"},
                ],
                "totalSize": 3,
                "done": True,
            }
        if tool == "get_record":
            return {
                "id": args.get("id", "MOCK_UNKNOWN"),
                "Name": "Mock Record",
                "CreatedDate": "2026-05-15T00:00:00Z",
            }
        if tool == "send_message":
            for required in ("to", "body"):
                if required not in args:
                    raise MCPInvalidArgs(f"send_message requires '{required}'")
            return {"sent": True, "to": args["to"], "message_id": f"msg_{uuid.uuid4().hex[:10]}"}
        raise MCPToolNotFound(f"unknown mock tool: {tool!r}")