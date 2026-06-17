"""
Current-user dependency + scoped-repo dependency for FastAPI routes.

Protected routes declare `user: User = Depends(get_current_user)` and build
their repo via `repo: ScopedRepo = Depends(get_scoped_repo_dep)`. The scoped
repo is bound to the authenticated user, so the route physically cannot read
across tenants.

D6 — dev escape hatch: when settings.auth_dev_mode is true (local dev / CLI),
an unauthenticated request auto-resolves to the seeded default user, so
existing tooling (run_plan_e2e, curl) keeps working without logging in. In
production auth_dev_mode is false and a missing/invalid session -> 401.
"""
from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException

from app.config import get_settings
from app.schemas.auth import User, UserStatus
from app.services.oauth.service import resolve_session
from app.services.oauth.sql_store import get_user_store, get_session_store
from app.services.scoping import ScopedRepo, get_scoped_repo

SESSION_COOKIE = "session"


async def get_current_user(session: str | None = Cookie(default=None)) -> User:
    settings = get_settings()
    user = None
    if session:
        user = await resolve_session(get_user_store(), get_session_store(), session)

    if user is None:
        # Dev escape hatch: auto-auth as the default user for local tooling.
        if getattr(settings, "auth_dev_mode", False):
            store = get_user_store()
            user = await store.get_by_id(settings.default_user_id)
            if user is None:
                # Seeded user missing — synthesize a minimal one so dev flows
                # still resolve (the seed migration should have created it).
                user = User(id=settings.default_user_id,
                            email="dev@local", display_name="Dev",
                            status=UserStatus.ACTIVE)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")

    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="account is not active")
    return user


async def get_scoped_repo_dep(user: User = Depends(get_current_user)) -> ScopedRepo:
    return get_scoped_repo(user.id)