"""
Auth + multi-tenant isolation tests.

The security-critical ones: user A must NEVER see, run, or mutate user B's
plans / automations / runs through the scoped repository. These are written
test-first because a cross-tenant leak is the worst failure this phase could
ship.

Run from backend/:  python -m tests.test_auth_scoping
"""
from __future__ import annotations

import asyncio
import sys

from app.schemas.auth import User
from app.schemas.plan import Plan, Step, StepKind
from app.schemas.automation import Automation
from app.services.scoping import ScopedRepo, AccessDenied
from app.services.auth.service import (
    InMemoryUserStore, InMemorySessionStore,
    register, login, resolve_session, logout, AuthError,
)


# ---------------------------------------------------------------------------
# A fake repo implementing the (now user_id-aware) Repository surface, so we
# can test ScopedRepo enforcement without a database.
# ---------------------------------------------------------------------------

class FakeRepo:
    def __init__(self):
        self.plans: dict[str, tuple[str, Plan]] = {}        # id -> (owner, plan)
        self.autos: dict[str, tuple[str, Automation]] = {}
        self.runs: dict[str, tuple[str, object]] = {}

    async def save_plan(self, plan, *, user_id=None):
        owner = user_id or "default"
        if plan.id in self.plans and user_id is not None and self.plans[plan.id][0] != user_id:
            raise PermissionError("not owner")
        self.plans[plan.id] = (owner, plan)

    async def get_plan(self, plan_id, *, user_id=None):
        rec = self.plans.get(plan_id)
        if not rec:
            return None
        if user_id is not None and rec[0] != user_id:
            return None
        return rec[1]

    async def list_plans(self, *, user_id=None):
        return [p for (o, p) in self.plans.values() if user_id is None or o == user_id]

    async def save_automation(self, auto, *, user_id=None):
        owner = user_id or auto.user_id or "default"
        if auto.id in self.autos and user_id is not None and self.autos[auto.id][0] != user_id:
            raise PermissionError("not owner")
        self.autos[auto.id] = (owner, auto)

    async def get_automation(self, auto_id, *, user_id=None):
        rec = self.autos.get(auto_id)
        if not rec:
            return None
        if user_id is not None and rec[0] != user_id:
            return None
        return rec[1]

    async def list_automations(self, *, user_id=None):
        return [a for (o, a) in self.autos.values() if user_id is None or o == user_id]

    async def save_run(self, run, *, user_id=None):
        self.runs[run.id] = (user_id or "default", run)

    async def get_run(self, run_id, *, user_id=None):
        rec = self.runs.get(run_id)
        if not rec:
            return None
        if user_id is not None and rec[0] not in (None, user_id):
            return None
        return rec[1]

    async def get_run_by_token_hash(self, token_hash):
        return None

    async def list_runs(self, automation_id=None, *, user_id=None):
        out = []
        for (o, r) in self.runs.values():
            if user_id is not None and o != user_id:
                continue
            out.append(r)
        return out


def _plan(pid="plan_1", goal="Create a lead"):
    return Plan(id=pid, goal=goal, summary="x",
                steps=[Step(id="s1", kind=StepKind.UI_ACTION, description="fill")])


def _auto(aid="auto_1", uid="alice", plan_id="plan_1"):
    return Automation(id=aid, user_id=uid, name="My Auto", plan_id=plan_id)


# ---------------------------------------------------------------------------
# Isolation tests
# ---------------------------------------------------------------------------

async def test_user_cannot_read_others_plan():
    repo = FakeRepo()
    alice = ScopedRepo(repo, "alice")
    bob = ScopedRepo(repo, "bob")
    await alice.save_plan(_plan("plan_a"))
    assert await alice.get_plan("plan_a") is not None
    assert await bob.get_plan("plan_a") is None, "bob must not read alice's plan"
    print("  cross-user plan read blocked")


async def test_list_is_scoped():
    repo = FakeRepo()
    alice = ScopedRepo(repo, "alice")
    bob = ScopedRepo(repo, "bob")
    await alice.save_plan(_plan("plan_a"))
    await bob.save_plan(_plan("plan_b"))
    alice_plans = await alice.list_plans()
    bob_plans = await bob.list_plans()
    assert {p.id for p in alice_plans} == {"plan_a"}
    assert {p.id for p in bob_plans} == {"plan_b"}
    print("  list_plans scoped per user")


async def test_user_cannot_overwrite_others_plan():
    repo = FakeRepo()
    alice = ScopedRepo(repo, "alice")
    bob = ScopedRepo(repo, "bob")
    await alice.save_plan(_plan("plan_a", goal="Alice's"))
    # Bob tries to overwrite alice's plan id -> PermissionError from repo.
    try:
        await bob.save_plan(_plan("plan_a", goal="Bob's hijack"))
        assert False, "bob overwrote alice's plan!"
    except PermissionError:
        pass
    # Alice's plan unchanged.
    assert (await alice.get_plan("plan_a")).goal == "Alice's"
    print("  cross-user plan overwrite blocked")


async def test_automation_ownership_forced():
    repo = FakeRepo()
    alice = ScopedRepo(repo, "alice")
    # An Automation claiming user_id='bob' submitted through alice's scoped
    # repo is REJECTED — we never silently reassign ownership (that could mask
    # a confused-deputy bug). The caller must submit their own id or none.
    sneaky = _auto("auto_x", uid="bob")
    try:
        await alice.save_automation(sneaky)
        assert False, "must reject automation claiming another user's id"
    except AccessDenied:
        pass
    # An automation with no/own owner is accepted and stamped to alice.
    ok = _auto("auto_y", uid="alice")
    await alice.save_automation(ok)
    got = await alice.get_automation("auto_y")
    assert got is not None and got.user_id == "alice"
    bob = ScopedRepo(repo, "bob")
    assert await bob.get_automation("auto_y") is None
    print("  automation with foreign owner rejected; own-owner stamped + scoped")


async def test_runs_scoped():
    repo = FakeRepo()
    alice = ScopedRepo(repo, "alice")
    bob = ScopedRepo(repo, "bob")

    class R:  # minimal run-like
        id = "run_1"
    await alice.save_run(R())
    assert await alice.get_run("run_1") is not None
    assert await bob.get_run("run_1") is None
    print("  run read scoped per user")


def test_scoped_repo_requires_user():
    repo = FakeRepo()
    try:
        ScopedRepo(repo, "")
        assert False, "empty user_id must be rejected"
    except ValueError:
        print("  ScopedRepo rejects empty user_id")


# ---------------------------------------------------------------------------
# Auth lifecycle tests
# ---------------------------------------------------------------------------

async def test_register_login_session_lifecycle():
    users, sessions = InMemoryUserStore(), InMemorySessionStore()
    u = await register(users, email="a@x.com", password="hunter2pass", display_name="A")
    assert u.id.startswith("user_")

    # duplicate registration rejected
    try:
        await register(users, email="a@x.com", password="another1234")
        assert False
    except AuthError:
        pass

    # wrong password rejected, uniform message
    try:
        await login(users, sessions, email="a@x.com", password="wrongwrong")
        assert False
    except AuthError as e:
        assert "invalid email or password" in str(e)

    # correct login -> token
    user, token = await login(users, sessions, email="a@x.com", password="hunter2pass")
    assert token and user.id == u.id

    # token resolves to the user
    resolved = await resolve_session(users, sessions, token)
    assert resolved is not None and resolved.id == u.id

    # logout revokes
    await logout(sessions, token)
    assert await resolve_session(users, sessions, token) is None
    print("  register/login/resolve/logout lifecycle ok")


async def test_unknown_email_uniform_failure():
    users, sessions = InMemoryUserStore(), InMemorySessionStore()
    try:
        await login(users, sessions, email="nobody@x.com", password="whatever1")
        assert False
    except AuthError as e:
        assert "invalid email or password" in str(e)
    print("  unknown email -> uniform failure (no user enumeration)")


async def test_password_never_plaintext():
    users = InMemoryUserStore()
    await register(users, email="b@x.com", password="sup3rs3cret", display_name="B")
    _, stored_hash = await users.get_by_email("b@x.com")
    assert "sup3rs3cret" not in stored_hash
    assert stored_hash.startswith("$argon2") or stored_hash.startswith("pbkdf2$")
    print("  password stored as hash, never plaintext")


def main():
    sync = [test_scoped_repo_requires_user]
    asyncs = [
        test_user_cannot_read_others_plan,
        test_list_is_scoped,
        test_user_cannot_overwrite_others_plan,
        test_automation_ownership_forced,
        test_runs_scoped,
        test_register_login_session_lifecycle,
        test_unknown_email_uniform_failure,
        test_password_never_plaintext,
    ]
    passed = 0
    for t in sync:
        print(f"- {t.__name__}"); t(); passed += 1
    for t in asyncs:
        print(f"- {t.__name__}"); asyncio.run(t()); passed += 1
    print(f"\nAll {passed} auth/scoping tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())