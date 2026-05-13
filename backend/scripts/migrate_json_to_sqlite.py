"""
One-shot migration: read existing .local_storage/{plans,runs,automations}/*.json
and insert them into SQLite via the new SqlRepo.

Idempotent — running twice is safe; existing rows are upserted.

Usage:
    cd backend
    python -m scripts.migrate_json_to_sqlite
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.schemas.automation import Automation
from app.schemas.plan import Plan
from app.schemas.run import Run
from app.services.run_repo import get_repository


async def migrate() -> int:
    settings = get_settings()
    root = Path(settings.local_storage_root)
    repo = get_repository()

    counts = {"plans": 0, "automations": 0, "runs": 0, "skipped": 0}

    plans_dir = root / "plans"
    if plans_dir.exists():
        for path in plans_dir.glob("*.json"):
            try:
                plan = Plan.model_validate_json(path.read_text())
                await repo.save_plan(plan)
                counts["plans"] += 1
            except Exception as e:
                print(f"  skipped {path.name}: {e}", file=sys.stderr)
                counts["skipped"] += 1

    autos_dir = root / "automations"
    if autos_dir.exists():
        for path in autos_dir.glob("*.json"):
            try:
                auto = Automation.model_validate_json(path.read_text())
                await repo.save_automation(auto)
                counts["automations"] += 1
            except Exception as e:
                print(f"  skipped {path.name}: {e}", file=sys.stderr)
                counts["skipped"] += 1

    runs_dir = root / "runs"
    if runs_dir.exists():
        for path in runs_dir.glob("*.json"):
            try:
                run = Run.model_validate_json(path.read_text())
                await repo.save_run(run)
                counts["runs"] += 1
            except Exception as e:
                print(f"  skipped {path.name}: {e}", file=sys.stderr)
                counts["skipped"] += 1

    print(
        f"Migrated: {counts['plans']} plans, {counts['automations']} automations, "
        f"{counts['runs']} runs ({counts['skipped']} skipped)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(migrate()))