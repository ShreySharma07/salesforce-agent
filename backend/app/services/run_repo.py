"""
Repository layer for Plans, Automations, Runs.

Two implementations:
  - JsonFileRepo: each record is a JSON file under .local_storage/{kind}/{id}.json.
                  zero setup, perfect for dev and the first 100 records.
  - PostgresRepo: same interface, real DB.  Stub today.

Switch via REPO_BACKEND env var. Concurrency-safe writes via temp file +
atomic rename. Querying is simple linear scan (fine for dev volumes).

When you flip to Postgres, no other code changes — same .save(), .get(),
.list_by_user() calls.
"""
from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from app.config import get_settings
from app.schemas.automation import Automation
from app.schemas.plan import Plan
from app.schemas.run import Run

T = TypeVar("T", Plan, Run, Automation)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class Repository(ABC):
    """Base interface every backend implements. Generic over record type
    to keep call sites tidy."""

    @abstractmethod
    async def save_plan(self, plan: Plan) -> None: ...
    @abstractmethod
    async def get_plan(self, plan_id: str) -> Plan | None: ...
    @abstractmethod
    async def list_plans(self) -> list[Plan]: ...

    @abstractmethod
    async def save_automation(self, auto: Automation) -> None: ...
    @abstractmethod
    async def get_automation(self, auto_id: str) -> Automation | None: ...
    @abstractmethod
    async def list_automations(self) -> list[Automation]: ...

    @abstractmethod
    async def save_run(self, run: Run) -> None: ...
    @abstractmethod
    async def get_run(self, run_id: str) -> Run | None: ...
    @abstractmethod
    async def list_runs(self, automation_id: str | None = None) -> list[Run]: ...


# ---------------------------------------------------------------------------
# JSON file implementation
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, data: str) -> None:
    """Write atomically: write to temp file in same dir, then rename.
    Avoids corruption if the process dies mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _json_dumps(obj) -> str:
    """Pydantic v2 .model_dump_json() doesn't take indent; this does."""
    return json.dumps(json.loads(obj.model_dump_json()), indent=2, default=str)


class JsonFileRepo(Repository):
    def __init__(self, root: Path):
        self.root = root
        self.plans_dir = root / "plans"
        self.runs_dir = root / "runs"
        self.autos_dir = root / "automations"
        for d in (self.plans_dir, self.runs_dir, self.autos_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- Plans ----------------------------------------------------------
    async def save_plan(self, plan: Plan) -> None:
        _atomic_write(self.plans_dir / f"{plan.id}.json", _json_dumps(plan))

    async def get_plan(self, plan_id: str) -> Plan | None:
        path = self.plans_dir / f"{plan_id}.json"
        if not path.exists():
            return None
        return Plan.model_validate_json(path.read_text())

    async def list_plans(self) -> list[Plan]:
        return sorted(
            (Plan.model_validate_json(p.read_text()) for p in self.plans_dir.glob("*.json")),
            key=lambda p: p.created_at,
            reverse=True,
        )

    # ---- Automations ----------------------------------------------------
    async def save_automation(self, auto: Automation) -> None:
        _atomic_write(self.autos_dir / f"{auto.id}.json", _json_dumps(auto))

    async def get_automation(self, auto_id: str) -> Automation | None:
        path = self.autos_dir / f"{auto_id}.json"
        if not path.exists():
            return None
        return Automation.model_validate_json(path.read_text())

    async def list_automations(self) -> list[Automation]:
        return sorted(
            (Automation.model_validate_json(p.read_text()) for p in self.autos_dir.glob("*.json")),
            key=lambda a: a.created_at,
            reverse=True,
        )

    # ---- Runs -----------------------------------------------------------
    async def save_run(self, run: Run) -> None:
        _atomic_write(self.runs_dir / f"{run.id}.json", _json_dumps(run))

    async def get_run(self, run_id: str) -> Run | None:
        path = self.runs_dir / f"{run_id}.json"
        if not path.exists():
            return None
        return Run.model_validate_json(path.read_text())

    async def list_runs(self, automation_id: str | None = None) -> list[Run]:
        runs = (Run.model_validate_json(p.read_text()) for p in self.runs_dir.glob("*.json"))
        if automation_id:
            runs = (r for r in runs if r.automation_id == automation_id)
        return sorted(
            runs,
            key=lambda r: r.started_at or datetime.min,
            reverse=True,
        )


# ---------------------------------------------------------------------------
# Postgres stub - reference implementation in comments
# ---------------------------------------------------------------------------

class PostgresRepo(Repository):
    """STUB. Implement when you have >100 saved records or multiple users.

    Sketch (using SQLAlchemy 2.0 async):

        async def save_plan(self, plan):
            async with self._sessionmaker() as session:
                stmt = pg_insert(plans_table).values(
                    id=plan.id, data=plan.model_dump_json(),
                ).on_conflict_do_update(
                    index_elements=['id'],
                    set_={'data': plan.model_dump_json(), 'updated_at': func.now()},
                )
                await session.execute(stmt)
                await session.commit()

    Migration plan when activating:
        1. Run `alembic upgrade head` to create tables
        2. Run `python -m scripts.migrate_json_to_postgres` to copy existing JSON files
        3. Set REPO_BACKEND=postgres in .env
        4. Restart backend
    """
    def __init__(self, dsn: str):
        raise NotImplementedError(
            "PostgresRepo is a stub. Set REPO_BACKEND=json for now. "
            "See app/services/run_repo.py docstring for the migration recipe."
        )

    async def save_plan(self, plan): ...
    async def get_plan(self, plan_id): ...
    async def list_plans(self): ...
    async def save_automation(self, auto): ...
    async def get_automation(self, auto_id): ...
    async def list_automations(self): ...
    async def save_run(self, run): ...
    async def get_run(self, run_id): ...
    async def list_runs(self, automation_id=None): ...


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_repository() -> Repository:
    settings = get_settings()
    backend = settings.repo_backend.lower()
    if backend == "json":
        return JsonFileRepo(Path(settings.local_storage_root))
    if backend == "postgres":
        return PostgresRepo(settings.database_url)
    raise ValueError(f"Unknown REPO_BACKEND={backend!r}")