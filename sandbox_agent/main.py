"""
HTTP server inside the sandbox container.

Endpoints:
  GET  /health      Liveness check
  POST /run         Execute a Plan; returns final RunResponse synchronously.

For Phase 1 we keep this synchronous — one container per run, the run
either finishes or the container is killed. Phase 2 will add async +
status polling for long-running plans.
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
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="GEMINI_API_KEY not set inside the container. Pass it via -e GEMINI_API_KEY=...",
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