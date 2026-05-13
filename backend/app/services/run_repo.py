"""
SQLAlchemy repository for Plans, Automations, Runs.

This is the SINGLE implementation of the Repository interface.
Pydantic models are the API contract; ORM models are persistence.
Conversion happens at this boundary.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import get_sessionmaker
from app.db.models import Automation as AutomationOrm
from app.db.models import Plan as PlanOrm
from app.db.models import Run as RunOrm
from app.schemas.automation import Automation
from app.schemas.plan import Plan
from app.schemas.run import Run


class Repository(ABC):
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


def _json(obj) -> dict:
    return obj.model_dump(mode="json")


def _enum_str(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


class SqlRepo(Repository):
    async def save_plan(self, plan: Plan) -> None:
        async with get_sessionmaker()() as session:
            existing = await session.get(PlanOrm, plan.id)
            payload = _json(plan)
            if existing:
                existing.payload = payload
                existing.status = _enum_str(plan.status)
                existing.version = plan.version
                existing.source_video_id = plan.source_video_id
                existing.updated_at = datetime.utcnow()
            else:
                session.add(PlanOrm(
                    id=plan.id,
                    user_id=get_settings().default_user_id,
                    payload=payload,
                    status=_enum_str(plan.status),
                    version=plan.version,
                    source_video_id=plan.source_video_id,
                ))
            await session.commit()

    async def get_plan(self, plan_id: str) -> Plan | None:
        async with get_sessionmaker()() as session:
            row = await session.get(PlanOrm, plan_id)
            if row is None:
                return None
            return Plan.model_validate(row.payload)

    async def list_plans(self) -> list[Plan]:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(PlanOrm).order_by(PlanOrm.created_at.desc())
            )
            return [Plan.model_validate(r.payload) for r in result.scalars().all()]

    async def save_automation(self, auto: Automation) -> None:
        async with get_sessionmaker()() as session:
            existing = await session.get(AutomationOrm, auto.id)
            payload = _json(auto)
            if existing:
                existing.payload = payload
                existing.name = auto.name
                existing.status = _enum_str(auto.status)
                existing.plan_id = auto.plan_id
                existing.total_runs = auto.total_runs
                existing.successful_runs = auto.successful_runs
                existing.last_run_at = auto.last_run_at
                existing.updated_at = datetime.utcnow()
            else:
                session.add(AutomationOrm(
                    id=auto.id,
                    user_id=auto.user_id or get_settings().default_user_id,
                    plan_id=auto.plan_id,
                    name=auto.name,
                    status=_enum_str(auto.status),
                    payload=payload,
                    total_runs=auto.total_runs,
                    successful_runs=auto.successful_runs,
                    last_run_at=auto.last_run_at,
                ))
            await session.commit()

    async def get_automation(self, auto_id: str) -> Automation | None:
        async with get_sessionmaker()() as session:
            row = await session.get(AutomationOrm, auto_id)
            if row is None:
                return None
            return Automation.model_validate(row.payload)

    async def list_automations(self) -> list[Automation]:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(AutomationOrm).order_by(AutomationOrm.created_at.desc())
            )
            return [Automation.model_validate(r.payload) for r in result.scalars().all()]

    async def save_run(self, run: Run) -> None:
        async with get_sessionmaker()() as session:
            existing = await session.get(RunOrm, run.id)
            payload = _json(run)
            cost_usd = float(run.cost.cost_usd) if run.cost else 0.0
            llm_calls = int(run.cost.llm_calls) if run.cost else 0
            if existing:
                existing.payload = payload
                existing.status = _enum_str(run.status)
                existing.sandbox_id = run.sandbox_id
                existing.live_view_url = run.live_view_url
                existing.started_at = run.started_at
                existing.finished_at = run.finished_at
                existing.error = run.error
                existing.summary = run.summary
                existing.cost_usd = cost_usd
                existing.llm_calls = llm_calls
            else:
                session.add(RunOrm(
                    id=run.id,
                    automation_id=run.automation_id,
                    plan_version=run.plan_version,
                    status=_enum_str(run.status),
                    triggered_by=_enum_str(run.triggered_by),
                    triggered_by_user=run.triggered_by_user,
                    sandbox_id=run.sandbox_id,
                    live_view_url=run.live_view_url,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    error=run.error,
                    summary=run.summary,
                    cost_usd=cost_usd,
                    llm_calls=llm_calls,
                    payload=payload,
                ))
            await session.commit()

    async def get_run(self, run_id: str) -> Run | None:
        async with get_sessionmaker()() as session:
            row = await session.get(RunOrm, run_id)
            if row is None:
                return None
            return Run.model_validate(row.payload)

    async def list_runs(self, automation_id: str | None = None) -> list[Run]:
        async with get_sessionmaker()() as session:
            stmt = select(RunOrm).order_by(RunOrm.created_at.desc())
            if automation_id:
                stmt = stmt.where(RunOrm.automation_id == automation_id)
            result = await session.execute(stmt)
            return [Run.model_validate(r.payload) for r in result.scalars().all()]


_repo: SqlRepo | None = None


def get_repository() -> Repository:
    global _repo
    if _repo is None:
        _repo = SqlRepo()
    return _repo