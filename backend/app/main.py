"""
Backend FastAPI app. Run with:

    python -m app.main

The backend's port is 8001 by default; sandbox containers use 8000.

Startup:
  1. Run pending Alembic migrations (if AUTO_MIGRATE=true)
  2. Bootstrap default user if none exists
  3. Verify sandbox image is present (warn loudly if not)
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import automations, credentials, mcp, oauth, plans, runs, sandbox_frontdoor, sandbox_llm
from app.config import get_settings
from app.db.base import close_engine, get_sessionmaker
from app.db.models import User
from app.api import automations, credentials, mcp, oauth, plans, runs, sandbox_frontdoor, sandbox_llm, auth_routes
from fastapi.middleware.cors import CORSMiddleware


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("backend")


def _is_migration_current() -> bool:
    """Read-only pre-check: is the DB already at the Alembic head revision?

    Uses a synchronous sqlite3 read (no write lock) so a running backend
    holding the database open does not cause a 2-minute busy-timeout wait.
    Falls back to False on any error so the subprocess still runs in
    ambiguous situations.
    """
    import sqlite3 as _sqlite3
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    try:
        settings = get_settings()
        if not settings.database_url.startswith("sqlite"):
            return False  # Postgres: always let alembic decide
        db_path = settings.database_url.split("///", 1)[-1]
        if not db_path or not Path(db_path).exists():
            return False  # DB not created yet

        backend_dir = Path(__file__).resolve().parent.parent
        cfg = AlembicConfig(str(backend_dir / "alembic.ini"))
        heads = set(ScriptDirectory.from_config(cfg).get_heads())
        if not heads:
            return False

        con = _sqlite3.connect(db_path, timeout=1.0)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        except _sqlite3.OperationalError:
            return False  # table missing — DB is new
        finally:
            con.close()

        return row is not None and row[0] in heads
    except Exception:
        return False  # any error → let the subprocess sort it out


async def _run_migrations() -> None:
    """Run Alembic migrations in a subprocess to eliminate the SQLAlchemy 2 +
    aiosqlite connection-state deadlock that occurs when alembic.command.upgrade()
    runs inside the same process that later opens async sessions."""
    settings = get_settings()

    if _is_migration_current():
        log.info("Migrations up to date")
        return

    log.info("Running database migrations (in subprocess)")
    storage_root = Path(settings.local_storage_root)
    storage_root.mkdir(parents=True, exist_ok=True)

    backend_dir = Path(__file__).resolve().parent.parent
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic",
        "-c", str(backend_dir / "alembic.ini"),
        "upgrade", "head",
        cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if stdout:
        for line in stdout.decode().splitlines():
            log.info("[alembic] %s", line)
    if proc.returncode != 0:
        raise RuntimeError(f"Alembic migrations failed (exit {proc.returncode})")
    log.info("Migrations up to date")

async def _ensure_default_user() -> None:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        existing = await session.get(User, settings.default_user_id)
        if existing is None:
            session.add(User(
                id=settings.default_user_id,
                email=None, name="Local Dev User", is_active=True,
            ))
            await session.commit()
            log.info("Created default user: %s", settings.default_user_id)


def _check_sandbox_image() -> None:
    settings = get_settings()
    if settings.sandbox_runner != "local_docker":
        return
    result = subprocess.run(
        ["docker", "image", "inspect", settings.sandbox_image],
        capture_output=True,
    )
    if result.returncode != 0:
        log.warning(
            "Sandbox image %r not found locally. Build with: "
            "docker build -t %s -f sandbox/Dockerfile .",
            settings.sandbox_image, settings.sandbox_image,
        )


def _check_vault_key() -> None:
    settings = get_settings()
    if not settings.vault_encryption_key:
        log.warning(
            "VAULT_ENCRYPTION_KEY not set. Credential operations will fail. "
            "Generate one with: python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _check_vault_key()
    if settings.auto_migrate:
       await _run_migrations()
    await _ensure_default_user()
    _check_sandbox_image()
    log.info(
        "Backend up. llm=%s sandbox=%s db=%s",
        settings.llm_provider, settings.sandbox_runner,
        "sqlite" if "sqlite" in settings.database_url else "postgres",
    )
    yield
    await close_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Agent Platform Backend", version="0.2.0", lifespan=lifespan)

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
    app.include_router(credentials.router)
    app.include_router(oauth.router)
    app.include_router(mcp.router)
    app.include_router(sandbox_frontdoor.router)
    app.include_router(sandbox_llm.router)

    @app.get("/health")
    def health() -> dict:
        s = get_settings()
        return {
            "status": "ok",
            "llm_provider": s.llm_provider,
            "sandbox_runner": s.sandbox_runner,
            "db": "sqlite" if "sqlite" in s.database_url else "postgres",
            "vault_configured": bool(s.vault_encryption_key),
        }
    return app


app = create_app()

app.include_router(auth_routes.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # explicit, NOT "*"
    allow_credentials=True,                    # REQUIRED for the session cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.backend_host,
        port=s.backend_port,
        reload=os.getenv("BACKEND_RELOAD") == "1",
        reload_excludes=[".local_storage/*", "*.json", "*.db", "*.db-journal"]
        if os.getenv("BACKEND_RELOAD") == "1" else None,
    )