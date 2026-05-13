"""
Backend FastAPI app. Run with:

    uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

Or via:

    python -m app.main

The backend's port is 8001 by default; sandbox containers use 8000
(don't collide). The frontend dashboard (Phase 1c) will hit this server.
"""
from __future__ import annotations

import logging
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import automations, plans, runs
from app.config import get_settings


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backend")


def _check_sandbox_image(image: str) -> None:
    """Warn loudly at startup if the sandbox image is missing so the
    developer knows before they try to run a plan and hit a confusing
    docker error 10 seconds into provisioning."""
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
    )
    if result.returncode != 0:
        log.warning(
            "\n"
            "  ╔══════════════════════════════════════════════════════╗\n"
            "  ║  WARNING: sandbox image %r not found locally.  ║\n"
            "  ║  Plans will fail at runtime until you build it.      ║\n"
            "  ║  Run:                                                 ║\n"
            "  ║    docker build -t %s -f sandbox/Dockerfile . ║\n"
            "  ╚══════════════════════════════════════════════════════╝",
            image, image,
        )


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.sandbox_runner == "local_docker":
            _check_sandbox_image(settings.sandbox_image)
        yield

    app = FastAPI(title="AI Agent Platform Backend", version="0.1.0",
                  lifespan=lifespan)

    # CORS - permissive for dev. Tighten for prod.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(plans.router)
    app.include_router(automations.router)
    app.include_router(runs.router)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "llm_provider": settings.llm_provider,
            "sandbox_runner": settings.sandbox_runner,
            "repo_backend": settings.repo_backend,
        }

    log.info(
        "Backend up. llm=%s sandbox=%s repo=%s",
        settings.llm_provider, settings.sandbox_runner, settings.repo_backend,
    )
    return app


app = create_app()


if __name__ == "__main__":
    import os
    import uvicorn
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.backend_host,
        port=s.backend_port,
        # reload=True breaks long-running BackgroundTasks (sandbox runs).
        # Set BACKEND_RELOAD=1 only when you're actively editing code.
        reload=os.getenv("BACKEND_RELOAD") == "1",
        reload_excludes=[".local_storage/*", "*.json"] if os.getenv("BACKEND_RELOAD") == "1" else None,
    )