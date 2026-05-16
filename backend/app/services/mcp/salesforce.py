"""
Salesforce MCP server.

Wraps the Salesforce REST API as a set of callable tools. Activates as
soon as a user has connected Salesforce via OAuth (credentials in vault).

Tools (v1 — common operations):
  create_lead          POST   /services/data/v60.0/sobjects/Lead
  create_record        POST   /services/data/v60.0/sobjects/{object_type}
  query                GET    /services/data/v60.0/query?q={soql}
  get_record           GET    /services/data/v60.0/sobjects/{object_type}/{id}
  update_record        PATCH  /services/data/v60.0/sobjects/{object_type}/{id}
  search               GET    /services/data/v60.0/search?q={sosl}

More tools (delete, file upload, bulk operations) added as needed.

Auth:
  credentials must contain access_token + instance_url.
  oauth/refresh.py auto-refreshes before this is called, so access_token
  is guaranteed valid (within 60s buffer) when call_tool() runs.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.services.mcp.base import (
    MCPAuthError,
    MCPError,
    MCPInvalidArgs,
    MCPServer,
    MCPToolNotFound,
    ToolSchema,
)


API_VERSION = "v60.0"


class SalesforceMCPServer(MCPServer):
    @property
    def name(self) -> str:
        return "salesforce"

    @property
    def credential_provider(self) -> str | None:
        return "salesforce"

    async def list_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="create_lead",
                description="Create a Lead record. Returns the new Lead's ID.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "FirstName": {"type": "string"},
                        "LastName": {"type": "string", "description": "Required by Salesforce"},
                        "Company": {"type": "string", "description": "Required by Salesforce"},
                        "Email": {"type": "string"},
                        "Phone": {"type": "string"},
                        "LeadSource": {"type": "string"},
                        "Status": {"type": "string", "default": "Open - Not Contacted"},
                    },
                    "required": ["LastName", "Company"],
                },
                examples=[{
                    "FirstName": "Bob", "LastName": "Smith",
                    "Company": "Acme Corp", "Email": "bob@acme.com",
                    "LeadSource": "Web",
                }],
            ),
            ToolSchema(
                name="create_record",
                description="Create any Salesforce record by object type (Lead, Contact, Account, custom__c, etc.)",
                args_schema={
                    "type": "object",
                    "properties": {
                        "object_type": {"type": "string", "description": "e.g. 'Lead', 'Contact', 'Account'"},
                        "fields": {"type": "object", "description": "Field name -> value map"},
                    },
                    "required": ["object_type", "fields"],
                },
            ),
            ToolSchema(
                name="query",
                description="Run a SOQL query. Returns records list and totalSize.",
                args_schema={
                    "type": "object",
                    "properties": {"soql": {"type": "string"}},
                    "required": ["soql"],
                },
                examples=[{"soql": "SELECT Id, Name, Email FROM Lead WHERE CreatedDate = TODAY LIMIT 10"}],
            ),
            ToolSchema(
                name="get_record",
                description="Fetch a single record by ID.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "object_type": {"type": "string"},
                        "id": {"type": "string"},
                    },
                    "required": ["object_type", "id"],
                },
            ),
            ToolSchema(
                name="update_record",
                description="Update fields on an existing record. Returns success.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "object_type": {"type": "string"},
                        "id": {"type": "string"},
                        "fields": {"type": "object"},
                    },
                    "required": ["object_type", "id", "fields"],
                },
            ),
            ToolSchema(
                name="search",
                description="Full-text search across records via SOSL.",
                args_schema={
                    "type": "object",
                    "properties": {"sosl": {"type": "string"}},
                    "required": ["sosl"],
                },
                examples=[{"sosl": "FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name), Lead(Id, FirstName, LastName)"}],
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
                "Salesforce not connected for this user. "
                "Run /oauth/salesforce/connect first."
            )
        access_token = credentials.get("access_token")
        instance_url = credentials.get("instance_url")
        if not access_token or not instance_url:
            raise MCPAuthError(
                "Salesforce credentials are missing access_token or instance_url."
            )

        async with httpx.AsyncClient(
            base_url=instance_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        ) as client:
            return await self._dispatch(client, tool, args)

    async def _dispatch(
        self, client: httpx.AsyncClient, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if tool == "create_lead":
            self._require(args, ["LastName", "Company"])
            return await self._create(client, "Lead", args)

        if tool == "create_record":
            self._require(args, ["object_type", "fields"])
            return await self._create(client, args["object_type"], args["fields"])

        if tool == "query":
            self._require(args, ["soql"])
            return await self._query(client, args["soql"])

        if tool == "get_record":
            self._require(args, ["object_type", "id"])
            return await self._get(client, args["object_type"], args["id"])

        if tool == "update_record":
            self._require(args, ["object_type", "id", "fields"])
            return await self._update(client, args["object_type"], args["id"], args["fields"])

        if tool == "search":
            self._require(args, ["sosl"])
            return await self._search(client, args["sosl"])

        raise MCPToolNotFound(f"unknown salesforce tool: {tool!r}")

    @staticmethod
    def _require(args: dict[str, Any], required: list[str]) -> None:
        missing = [k for k in required if k not in args]
        if missing:
            raise MCPInvalidArgs(f"missing required args: {missing}")

    async def _create(
        self, client: httpx.AsyncClient, object_type: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        r = await client.post(
            f"/services/data/{API_VERSION}/sobjects/{object_type}",
            json=fields,
        )
        self._raise_for_status(r)
        body = r.json()
        return {
            "id": body.get("id"),
            "success": body.get("success", True),
            "object_type": object_type,
        }

    async def _query(self, client: httpx.AsyncClient, soql: str) -> dict[str, Any]:
        r = await client.get(
            f"/services/data/{API_VERSION}/query",
            params={"q": soql},
        )
        self._raise_for_status(r)
        body = r.json()
        return {
            "records": body.get("records", []),
            "totalSize": body.get("totalSize", 0),
            "done": body.get("done", True),
            "nextRecordsUrl": body.get("nextRecordsUrl"),
        }

    async def _get(
        self, client: httpx.AsyncClient, object_type: str, record_id: str
    ) -> dict[str, Any]:
        r = await client.get(
            f"/services/data/{API_VERSION}/sobjects/{object_type}/{record_id}",
        )
        self._raise_for_status(r)
        return r.json()

    async def _update(
        self,
        client: httpx.AsyncClient,
        object_type: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        r = await client.patch(
            f"/services/data/{API_VERSION}/sobjects/{object_type}/{record_id}",
            json=fields,
        )
        self._raise_for_status(r)
        # Salesforce returns 204 No Content on success
        return {"success": True, "id": record_id, "object_type": object_type}

    async def _search(self, client: httpx.AsyncClient, sosl: str) -> dict[str, Any]:
        r = await client.get(
            f"/services/data/{API_VERSION}/search",
            params={"q": sosl},
        )
        self._raise_for_status(r)
        return r.json()

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code == 401:
            raise MCPAuthError(
                "Salesforce returned 401. Token may be invalid or revoked. "
                "User needs to re-connect via /oauth/salesforce/connect."
            )
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:500]
            raise MCPError(f"Salesforce API error {r.status_code}: {detail}")