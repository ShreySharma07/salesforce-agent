"""
Gmail MCP server.

Wraps Gmail API as a set of callable tools. Activates as soon as a user
has connected Google via OAuth.

Tools (v1 — read-only + send):
  list_messages    GET    /gmail/v1/users/me/messages?q={query}
  get_message      GET    /gmail/v1/users/me/messages/{id}
  send_message     POST   /gmail/v1/users/me/messages/send
  list_labels      GET    /gmail/v1/users/me/labels
  modify_message   POST   /gmail/v1/users/me/messages/{id}/modify

Gmail's query syntax (q parameter) supports things like:
  from:boss@company.com
  is:unread
  newer_than:1d
  has:attachment
  subject:"Internships"
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

import httpx

from app.services.mcp.base import (
    MCPAuthError,
    MCPError,
    MCPInvalidArgs,
    MCPServer,
    MCPToolNotFound,
    ToolSchema,
)


GMAIL_BASE = "https://gmail.googleapis.com"


class GmailMCPServer(MCPServer):
    @property
    def name(self) -> str:
        return "gmail"

    @property
    def credential_provider(self) -> str | None:
        return "google"

    async def list_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="list_messages",
                description="Search for messages using Gmail query syntax. Returns list of message IDs + summary metadata.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "description": "Gmail query, e.g. 'from:x@y.com is:unread'"},
                        "max_results": {"type": "integer", "default": 25, "maximum": 500},
                    },
                },
                examples=[
                    {"q": "from:noreply@swelist.com newer_than:1d"},
                    {"q": "is:unread is:important", "max_results": 10},
                ],
            ),
            ToolSchema(
                name="get_message",
                description="Get the full content of one message by ID. Includes subject, from, to, body (plain text + HTML).",
                args_schema={
                    "type": "object",
                    "properties": {"message_id": {"type": "string"}},
                    "required": ["message_id"],
                },
            ),
            ToolSchema(
                name="send_message",
                description="Send an email from the authenticated user.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string", "description": "Plain text body"},
                        "cc": {"type": "string", "description": "Comma-separated"},
                        "bcc": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
            ToolSchema(
                name="list_labels",
                description="List all labels in the user's mailbox (Inbox, Sent, custom labels, etc.)",
                args_schema={"type": "object", "properties": {}},
            ),
            ToolSchema(
                name="modify_message",
                description="Add or remove labels on a message (e.g. mark read, archive, star).",
                args_schema={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "add_labels": {"type": "array", "items": {"type": "string"}},
                        "remove_labels": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["message_id"],
                },
                examples=[
                    {"message_id": "abc123", "remove_labels": ["UNREAD"]},
                    {"message_id": "abc123", "add_labels": ["STARRED"]},
                ],
            ),
        ]

    async def call_tool(
        self,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not credentials:
            raise MCPAuthError(
                "Gmail not connected. Run /oauth/google/connect first."
            )
        access_token = credentials.get("access_token")
        if not access_token:
            raise MCPAuthError("Google credentials missing access_token.")

        async with httpx.AsyncClient(
            base_url=GMAIL_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        ) as client:
            return await self._dispatch(client, tool, args)

    async def _dispatch(
        self, client: httpx.AsyncClient, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if tool == "list_messages":
            return await self._list_messages(
                client, q=args.get("q"), max_results=int(args.get("max_results", 25))
            )
        if tool == "get_message":
            self._require(args, ["message_id"])
            return await self._get_message(client, args["message_id"])
        if tool == "send_message":
            self._require(args, ["to", "subject", "body"])
            return await self._send_message(
                client, to=args["to"], subject=args["subject"], body=args["body"],
                cc=args.get("cc"), bcc=args.get("bcc"),
            )
        if tool == "list_labels":
            return await self._list_labels(client)
        if tool == "modify_message":
            self._require(args, ["message_id"])
            return await self._modify_message(
                client, args["message_id"],
                add_labels=args.get("add_labels", []),
                remove_labels=args.get("remove_labels", []),
            )
        raise MCPToolNotFound(f"unknown gmail tool: {tool!r}")

    @staticmethod
    def _require(args: dict[str, Any], required: list[str]) -> None:
        missing = [k for k in required if k not in args]
        if missing:
            raise MCPInvalidArgs(f"missing required args: {missing}")

    async def _list_messages(
        self, client: httpx.AsyncClient, q: str | None, max_results: int
    ) -> dict[str, Any]:
        params = {"maxResults": min(max_results, 500)}
        if q:
            params["q"] = q
        r = await client.get("/gmail/v1/users/me/messages", params=params)
        self._raise_for_status(r)
        body = r.json()
        return {
            "messages": body.get("messages", []),
            "resultSizeEstimate": body.get("resultSizeEstimate", 0),
            "nextPageToken": body.get("nextPageToken"),
        }

    async def _get_message(
        self, client: httpx.AsyncClient, message_id: str
    ) -> dict[str, Any]:
        r = await client.get(
            f"/gmail/v1/users/me/messages/{message_id}",
            params={"format": "full"},
        )
        self._raise_for_status(r)
        msg = r.json()
        return _parse_message(msg)

    async def _send_message(
        self,
        client: httpx.AsyncClient,
        *,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
    ) -> dict[str, Any]:
        em = EmailMessage()
        em["To"] = to
        em["Subject"] = subject
        if cc:
            em["Cc"] = cc
        if bcc:
            em["Bcc"] = bcc
        em.set_content(body)
        raw = base64.urlsafe_b64encode(em.as_bytes()).decode("ascii")
        r = await client.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": raw},
        )
        self._raise_for_status(r)
        body_json = r.json()
        return {
            "id": body_json.get("id"),
            "thread_id": body_json.get("threadId"),
            "label_ids": body_json.get("labelIds", []),
        }

    async def _list_labels(self, client: httpx.AsyncClient) -> dict[str, Any]:
        r = await client.get("/gmail/v1/users/me/labels")
        self._raise_for_status(r)
        return r.json()

    async def _modify_message(
        self,
        client: httpx.AsyncClient,
        message_id: str,
        *,
        add_labels: list[str],
        remove_labels: list[str],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if add_labels:
            payload["addLabelIds"] = add_labels
        if remove_labels:
            payload["removeLabelIds"] = remove_labels
        r = await client.post(
            f"/gmail/v1/users/me/messages/{message_id}/modify", json=payload
        )
        self._raise_for_status(r)
        return r.json()

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code == 401:
            raise MCPAuthError(
                "Gmail returned 401. Token may be invalid. "
                "Re-connect via /oauth/google/connect."
            )
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:500]
            raise MCPError(f"Gmail API error {r.status_code}: {detail}")


def _parse_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert Gmail's nested message structure to a flat readable dict."""
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "label_ids": msg.get("labelIds", []),
        "snippet": msg.get("snippet"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "subject": headers.get("subject"),
        "date": headers.get("date"),
        "body_text": _extract_body(msg.get("payload", {}), prefer="text/plain"),
        "body_html": _extract_body(msg.get("payload", {}), prefer="text/html"),
    }


def _extract_body(payload: dict[str, Any], prefer: str) -> str | None:
    """Walk multi-part MIME structure, decode the requested mime type."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    if mime == prefer and data:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        result = _extract_body(part, prefer)
        if result:
            return result
    # Fallback: any text content
    if mime.startswith("text/") and data:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
    return None