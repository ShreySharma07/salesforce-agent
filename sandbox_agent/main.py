"""
HTTP server inside the sandbox container.

Endpoints:
  GET  /health      Liveness check
  POST /run         Execute a Plan; returns final RunResponse synchronously.

For Phase 1 we keep this synchronous — one container per run, the run
either finishes or the container is killed. Phase 2 will add async +
status polling for long-running plans.

Note: the sandbox no longer holds an LLM API key. LLM calls are proxied
through the backend (/sandbox/llm/generate), so the only startup
requirement is BACKEND_MCP_URL — the address the sandbox uses to reach
the backend for both LLM and MCP calls.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from sandbox_agent.executor import run_plan
from sandbox_agent.schemas import RunRequest, RunResponse


app = FastAPI(title="Sandbox Agent", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "display": os.getenv("DISPLAY", "(unset)")}


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    # The sandbox reaches the backend for LLM (proxy) and MCP calls.
    # Without BACKEND_MCP_URL it cannot function — fail fast and clearly.
    if not os.getenv("BACKEND_MCP_URL"):
        raise HTTPException(
            status_code=400,
            detail=(
                "BACKEND_MCP_URL not set inside the container. The backend "
                "must inject it at sandbox spawn so the agent can reach the "
                "LLM proxy and MCP endpoints."
            ),
        )
    return run_plan(req)


@app.exception_handler(Exception)
def unhandled(_request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "sandbox_agent.main:app",
        host="0.0.0.0",
        port=int(os.getenv("AGENT_PORT", "8000")),
        log_level="info",
    )