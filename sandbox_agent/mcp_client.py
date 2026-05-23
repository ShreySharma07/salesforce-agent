"""
Sandbox MCP client.

Calls the backend's /mcp endpoint to invoke MCP tools. The sandbox never
holds credentials — backend handles that.

Phase 2b: every call carries a per-run bearer token (RUN_TOKEN), injected
into the container at spawn. The backend validates it against the hash
stored on the Run, so a compromised sandbox can only act for its own run
and cannot swap run_id to reach another user's integrations.

Environment variables (set by the backend when spawning the sandbox):
  BACKEND_MCP_URL    base URL of the backend (e.g. http://host.docker.internal:8001)
  RUN_ID             the run this sandbox is executing
  RUN_TOKEN          per-run secret; sent as Authorization: Bearer <token>
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx


class MCPClientError(Exception):
    pass


class MCPClient:
    def __init__(
        self,
        base_url: str | None = None,
        run_id: str | None = None,
        run_token: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or os.getenv("BACKEND_MCP_URL", "")).rstrip("/")
        self.run_id = run_id or os.getenv("RUN_ID")
        self.run_token = run_token or os.getenv("RUN_TOKEN")
        self.timeout = timeout
        if not self.base_url:
            raise MCPClientError(
                "BACKEND_MCP_URL not set. Backend must inject this env var "
                "when spawning the sandbox."
            )

    def call(
        self, server: str, tool: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a tool. Returns the parsed result dict on success.
        Raises MCPClientError on any non-2xx response."""
        url = f"{self.base_url}/mcp/{server}/{tool}"
        body = {"args": args or {}, "run_id": self.run_id}
        headers = {}
        if self.run_token:
            headers["Authorization"] = f"Bearer {self.run_token}"
        start = time.monotonic()

        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(url, json=body, headers=headers)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text[:500]
            raise MCPClientError(
                f"{server}/{tool} returned {r.status_code} ({elapsed_ms}ms): {detail}"
            )

        body_json = r.json()
        if not body_json.get("ok"):
            raise MCPClientError(f"{server}/{tool} returned ok=false: {body_json}")
        return body_json["result"]