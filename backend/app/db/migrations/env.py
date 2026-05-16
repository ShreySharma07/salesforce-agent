"""Alembic environment. Reads DB URL from our Settings + uses our Base metadata.

Migrations run synchronously - simpler, more reliable than async-in-thread.
We convert the async DB URL to a sync equivalent for the migration only.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401 - register models with Base.metadata

config = context.config


def _sync_url(async_url: str) -> str:
    """Strip async driver suffix so Alembic can use sync drivers."""
    return (
        async_url
        .replace("+aiosqlite", "")
        .replace("+asyncpg", "+psycopg")
    )


# Override URL with the sync version
config.set_main_option("sqlalchemy.url", _sync_url(get_settings().database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite ALTER TABLE compatibility
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()