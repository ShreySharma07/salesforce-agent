"""
One-off: update automations where user_id = "local_user" → "user_local".

Updates both the user_id column and the user_id field inside the payload JSON,
since get_automation() reads the Pydantic object from row.payload.

Usage:
    cd backend
    python -m scripts.fix_automation_user_id
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.db.base import get_sessionmaker
from app.db.models import Automation as AutomationOrm


OLD_USER_ID = "local_user"
NEW_USER_ID = "user_local"


async def fix() -> int:
    async with get_sessionmaker()() as session:
        # Fetch all automations where either the column OR the payload user_id is wrong.
        result = await session.execute(select(AutomationOrm))
        all_rows = result.scalars().all()

        rows = [
            r for r in all_rows
            if r.user_id == OLD_USER_ID
            or (isinstance(r.payload, dict) and r.payload.get("user_id") == OLD_USER_ID)
        ]

        if not rows:
            print(f"No automations found with user_id='{OLD_USER_ID}'. Nothing to do.")
            return 0

        for row in rows:
            row.user_id = NEW_USER_ID
            payload = copy.deepcopy(row.payload)
            if isinstance(payload, str):
                payload = json.loads(payload)
            if payload.get("user_id") == OLD_USER_ID:
                payload["user_id"] = NEW_USER_ID
            row.payload = payload
            flag_modified(row, "payload")

        await session.commit()

        print(f"Updated {len(rows)} automation(s):")
        for row in rows:
            print(f"  {row.id}  user_id → {NEW_USER_ID}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(fix()))
