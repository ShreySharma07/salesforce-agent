"""
SQL-backed user + session stores for auth.

Mirror the InMemoryUserStore / InMemorySessionStore interfaces from
auth/service.py, so swapping persistence never touches the auth logic
(same pattern as the memory stores). Reads/writes the User, Session ORM
rows. The ORM `User.name` column maps to the Pydantic `display_name`.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.base import get_sessionmaker
from app.db.models import Session as SessionOrm
from app.db.models import User as UserOrm
from app.schemas.auth import Session, User, UserStatus


def _orm_to_user(row: UserOrm) -> User:
    return User(
        id=row.id,
        email=row.email or "",
        display_name=row.name or "",
        status=UserStatus.ACTIVE if row.is_active else UserStatus.SUSPENDED,
        created_at=row.created_at,
    )


class SqlUserStore:
    async def get_by_email(self, email: str):
        async with get_sessionmaker()() as s:
            row = (await s.execute(
                select(UserOrm).where(UserOrm.email == email.lower())
            )).scalar_one_or_none()
            if row is None:
                return None
            return _orm_to_user(row), (row.password_hash or "")

    async def get_by_id(self, user_id: str):
        async with get_sessionmaker()() as s:
            row = await s.get(UserOrm, user_id)
            return _orm_to_user(row) if row else None

    async def create(self, user: User, password_hash: str) -> None:
        async with get_sessionmaker()() as s:
            s.add(UserOrm(
                id=user.id, email=user.email.lower(),
                name=user.display_name or None,
                password_hash=password_hash, is_active=True,
            ))
            await s.commit()


class SqlSessionStore:
    async def add(self, session: Session) -> None:
        async with get_sessionmaker()() as s:
            s.add(SessionOrm(
                id=session.id, user_id=session.user_id,
                token_hash=session.token_hash, revoked=session.revoked,
                expires_at=session.expires_at,
            ))
            await s.commit()

    async def get_by_token_hash(self, token_hash: str):
        async with get_sessionmaker()() as s:
            row = (await s.execute(
                select(SessionOrm).where(SessionOrm.token_hash == token_hash)
            )).scalar_one_or_none()
            if row is None:
                return None
            return Session(
                id=row.id, user_id=row.user_id, token_hash=row.token_hash,
                created_at=row.created_at, expires_at=row.expires_at,
                revoked=row.revoked,
            )

    async def revoke(self, session_id: str) -> None:
        async with get_sessionmaker()() as s:
            row = await s.get(SessionOrm, session_id)
            if row is not None:
                row.revoked = True
                await s.commit()


# Singletons (same pattern as get_repository).
_user_store: SqlUserStore | None = None
_session_store: SqlSessionStore | None = None


def get_user_store() -> SqlUserStore:
    global _user_store
    if _user_store is None:
        _user_store = SqlUserStore()
    return _user_store


def get_session_store() -> SqlSessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SqlSessionStore()
    return _session_store