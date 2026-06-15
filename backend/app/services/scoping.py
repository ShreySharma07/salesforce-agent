"""
Scoping guard — the multi-tenant security boundary.

The base SqlRepo has NO user filtering: get_plan / list_runs / etc. return
ANY user's data. That is correct for the per-run-token internal paths (the
sandbox calling back must look up a Run by token hash regardless of user),
but it is a cross-tenant LEAK if exposed to user-facing API routes unchecked.

This guard wraps the repository and REQUIRES a user_id on every user-facing
read and write. It:
  - filters reads to the owner (returns None / empty instead of another
    user's data),
  - verifies ownership on writes/updates (raises if a user tries to mutate
    something they don't own),
  - stamps new objects with the owner.

Design choice: enforcement lives HERE, at one chokepoint, not sprinkled
across route handlers — so a forgotten `where user_id=` in a route cannot
leak data. Routes get a ScopedRepo bound to the authenticated user; they
literally cannot ask for "everyone's" data through it.

The raw unscoped Repository remains available ONLY to internal token-auth
paths (sandbox_llm / mcp / frontdoor resolving a Run by token hash), which
are themselves protected by the per-run token.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas.automation import Automation
from app.schemas.plan import Plan
from app.schemas.run import Run

if TYPE_CHECKING:
    from app.services.run_repo import Repository


class AccessDenied(Exception):
    """Raised when a user attempts to access or mutate a resource they do not
    own. Routes translate this to 403/404."""
    pass


def _owner_of_automation(auto: Automation) -> str | None:
    return getattr(auto, "user_id", None)


class ScopedRepo:
    """A repository view bound to ONE user. Every method filters/stamps by
    that user_id. Obtained via get_scoped_repo(user_id); never constructed
    with someone else's id in a route."""

    def __init__(self, repo: "Repository", user_id: str) -> None:
        if not user_id:
            # Fail loud: a scoped repo with no user is a bug, not a default.
            raise ValueError("ScopedRepo requires a non-empty user_id")
        self._repo = repo
        self._uid = user_id

    @property
    def user_id(self) -> str:
        return self._uid

    # ----- Plans -----
    async def save_plan(self, plan: Plan, *, owner_user_id: str | None = None) -> None:
        # Always stamp with THIS user; ignore any caller-supplied owner that
        # isn't us (prevents creating data under another user's id).
        owner = owner_user_id or self._uid
        if owner != self._uid:
            raise AccessDenied("cannot create a plan owned by another user")
        # The base repo stamps owner internally today; we pass through and the
        # ownership column is set to self._uid by the (updated) repo.
        await self._repo.save_plan(plan, user_id=self._uid)

    async def get_plan(self, plan_id: str) -> Plan | None:
        return await self._repo.get_plan(plan_id, user_id=self._uid)

    async def list_plans(self) -> list[Plan]:
        return await self._repo.list_plans(user_id=self._uid)

    # ----- Automations -----
    async def save_automation(self, auto: Automation) -> None:
        owner = _owner_of_automation(auto)
        if owner and owner != self._uid:
            raise AccessDenied("cannot save an automation owned by another user")
        # Force ownership to this user.
        try:
            auto.user_id = self._uid
        except Exception:
            pass
        await self._repo.save_automation(auto, user_id=self._uid)

    async def get_automation(self, auto_id: str) -> Automation | None:
        return await self._repo.get_automation(auto_id, user_id=self._uid)

    async def list_automations(self) -> list[Automation]:
        return await self._repo.list_automations(user_id=self._uid)

    # ----- Runs -----
    async def save_run(self, run: Run) -> None:
        await self._repo.save_run(run, user_id=self._uid)

    async def get_run(self, run_id: str) -> Run | None:
        return await self._repo.get_run(run_id, user_id=self._uid)

    async def list_runs(self, automation_id: str | None = None) -> list[Run]:
        return await self._repo.list_runs(automation_id=automation_id, user_id=self._uid)


def get_scoped_repo(user_id: str) -> ScopedRepo:
    from app.services.run_repo import get_repository
    return ScopedRepo(get_repository(), user_id)