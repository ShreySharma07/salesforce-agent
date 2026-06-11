"""
SQL-backed memory stores.

Mirror the in-memory store interfaces (ProcedureStore / EpisodeStore /
lesson store) so the agent pipeline never changes — same pattern as
run_repo's SqlRepo. Procedures, episodes, and lessons persist across backend
restarts (the in-memory stores evaporate, which is fine for tests but not for
a real learning system).

Storage approach: one table per memory kind, the full pydantic object stored
as JSON in a payload column, plus the columns we actually query on
(user_id + the retrieval key). This matches how the rest of this codebase
stores rich objects (the Run/Plan rows keep a JSON payload alongside indexed
columns) and avoids a brittle wide schema that has to migrate every time a
memory field is added.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, get_sessionmaker
from app.schemas.memory import Episode, Lesson, Procedure


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class ProcedureRow(Base):
    __tablename__ = "memory_procedures"
    # (user_id, task_signature) is the natural key; id is the surrogate PK.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    task_signature: Mapped[str] = mapped_column(String(512), index=True)
    payload: Mapped[str] = mapped_column(Text)          # Procedure as JSON
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class EpisodeRow(Base):
    __tablename__ = "memory_episodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    situation_key: Mapped[str] = mapped_column(String(512), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)          # Episode as JSON
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class LessonRow(Base):
    __tablename__ = "memory_lessons"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[str] = mapped_column(Text)          # Lesson as JSON
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Procedure store (SQL)
# ---------------------------------------------------------------------------

class SqlProcedureStore:
    """SQL-backed ProcedureStore. Same interface as InMemoryProcedureStore."""

    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory or get_sessionmaker()

    async def get_by_signature(self, user_id: str, signature: str) -> Procedure | None:
        async with self._sf() as s:  #type:AsyncSession
            row = (await s.execute(
                select(ProcedureRow).where(
                    ProcedureRow.user_id == user_id,
                    ProcedureRow.task_signature == signature,
                )
            )).scalar_one_or_none()
            if row is None:
                return None
            return Procedure.model_validate_json(row.payload)

    async def save(self, proc: Procedure) -> None:
        async with self._sf() as s:
            row = (await s.execute(
                select(ProcedureRow).where(ProcedureRow.id == proc.id)
            )).scalar_one_or_none()
            payload = proc.model_dump_json()
            if row is None:
                # Also guard against a different id holding the same (user,sig).
                existing = (await s.execute(
                    select(ProcedureRow).where(
                        ProcedureRow.user_id == proc.user_id,
                        ProcedureRow.task_signature == proc.task_signature,
                    )
                )).scalar_one_or_none()
                if existing is not None:
                    existing.payload = payload
                    existing.updated_at = datetime.utcnow()
                else:
                    s.add(ProcedureRow(
                        id=proc.id, user_id=proc.user_id,
                        task_signature=proc.task_signature, payload=payload,
                        updated_at=datetime.utcnow(),
                    ))
            else:
                row.payload = payload
                row.task_signature = proc.task_signature
                row.updated_at = datetime.utcnow()
            await s.commit()

    async def list_for_user(self, user_id: str) -> list[Procedure]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(ProcedureRow).where(ProcedureRow.user_id == user_id)
            )).scalars().all()
            return [Procedure.model_validate_json(r.payload) for r in rows]


# ---------------------------------------------------------------------------
# Episode store (SQL)
# ---------------------------------------------------------------------------

class SqlEpisodeStore:
    """SQL-backed EpisodeStore. Same interface + the same de-dup-by-recurrence
    semantics as InMemoryEpisodeStore (bump seen_count instead of duplicating)."""

    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory or get_sessionmaker()

    async def add(self, ep: Episode) -> None:
        async with self._sf() as s:
            rows = (await s.execute(
                select(EpisodeRow).where(
                    EpisodeRow.user_id == ep.user_id,
                    EpisodeRow.situation_key == ep.situation_key,
                )
            )).scalars().all()
            for row in rows:
                existing = Episode.model_validate_json(row.payload)
                if existing.kind == ep.kind and existing.what_happened == ep.what_happened:
                    existing.seen_count += 1
                    existing.occurred_at = ep.occurred_at
                    if ep.resolution and not existing.resolution:
                        existing.resolution = ep.resolution
                    row.payload = existing.model_dump_json()
                    row.updated_at = datetime.utcnow()
                    await s.commit()
                    return
            s.add(EpisodeRow(
                id=ep.id, user_id=ep.user_id, situation_key=ep.situation_key,
                kind=ep.kind.value, payload=ep.model_dump_json(),
                updated_at=datetime.utcnow(),
            ))
            await s.commit()

    async def find(self, user_id: str, situation_key: str) -> list[Episode]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(EpisodeRow).where(
                    EpisodeRow.user_id == user_id,
                    EpisodeRow.situation_key == situation_key,
                )
            )).scalars().all()
            return [Episode.model_validate_json(r.payload) for r in rows]

    async def list_for_user(self, user_id: str) -> list[Episode]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(EpisodeRow).where(EpisodeRow.user_id == user_id)
            )).scalars().all()
            return [Episode.model_validate_json(r.payload) for r in rows]


# ---------------------------------------------------------------------------
# Lesson store (SQL)
# ---------------------------------------------------------------------------

class SqlLessonStore:
    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory or get_sessionmaker()

    async def add_many(self, lessons: list[Lesson]) -> None:
        if not lessons:
            return
        async with self._sf() as s:
            for l in lessons:
                s.add(LessonRow(
                    id=l.id, user_id=l.user_id, payload=l.model_dump_json(),
                    created_at=l.created_at,
                ))
            await s.commit()

    async def list_for_user(self, user_id: str) -> list[Lesson]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(LessonRow).where(LessonRow.user_id == user_id)
            )).scalars().all()
            return [Lesson.model_validate_json(r.payload) for r in rows]