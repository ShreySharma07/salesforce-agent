"""
Sandbox MCP client.

Calls the backend's /mcp endpoint to invoke MCP tools. The sandbox never
holds credentials — backend handles that. Sandbox just sends:

    POST {BACKEND_MCP_URL}/mcp/{server}/{tool}
    Body: {"args": {...}, "run_id": "{RUN_ID}"}

Backend resolves run_id -> user -> credentials -> actual API call.

Environment variables this client expects (set by backend when spawning sandbox):
  BACKEND_MCP_URL    base URL of the backend (e.g. http://host.docker.internal:8001)
  RUN_ID             the run this sandbox is executing
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
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or os.getenv("BACKEND_MCP_URL", "")).rstrip("/")
        self.run_id = run_id or os.getenv("RUN_ID")
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
        Raises MCPClientError on any non-2xx response or ok=false."""
        url = f"{self.base_url}/mcp/{server}/{tool}"
        body = {"args": args or {}, "run_id": self.run_id}
        start = time.monotonic()

        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(url, json=body)

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
            raise MCPClientError(
                f"{server}/{tool} returned ok=false: {body_json}"
            )
        return body_json["result"]