"""
SQLAlchemy 2.0 async setup.

One engine, one session factory, one declarative base. Works against:
  - sqlite+aiosqlite:///./dev.db   (dev)
  - postgresql+psycopg://...        (prod)

The async session is what every repository and API endpoint uses.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """All ORM models inherit from this."""


# ---------------------------------------------------------------------------
# Engine + session factory (singletons, created at module import)
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        # echo=False - we don't want SQL noise in logs
        # pool_pre_ping for connection liveness checks against Postgres
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            # SQLite-specific tuning: same-thread access from async
            connect_args=(
                {"check_same_thread": False}
                if settings.database_url.startswith("sqlite")
                else {}
            ),
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Use as `session: AsyncSession = Depends(get_session)`.
    Each request gets a fresh session, auto-closed when the request finishes."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    """For graceful shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None