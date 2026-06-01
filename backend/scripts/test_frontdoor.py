# scripts/test_frontdoor.py
import asyncio
from app.db.base import get_sessionmaker
from app.api.sandbox_frontdoor import _get_sf_token
from app.services.frontdoor import build_salesforce_frontdoor

async def main():
    async with get_sessionmaker()() as s:
        token, instance = await _get_sf_token(s, "user_local")
        r = await build_salesforce_frontdoor(access_token=token, instance_url=instance)
        print("FRONTDOOR URL:", r.frontdoor_url)
asyncio.run(main())