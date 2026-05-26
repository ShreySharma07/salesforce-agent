"""
Salesforce FrontDoor via the OAuth2 singleaccess endpoint.

singleaccess takes a valid access token and returns a ONE-TIME, short-lived
URL that logs a browser straight into the Salesforce UI. The raw access
token is never placed in a browser-visible URL — only the single-use
frontdoor URL is. This is the modern replacement for the older
`frontdoor.jsp?sid=<token>` trick.

Docs: Salesforce 'Use the OAuth 2.0 Single-Access Endpoint'.

This module only BUILDS the request and parses the response. Fetching the
token from the vault and refreshing it lives in the API layer, which has
the DB session.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx


log = logging.getLogger("frontdoor")


class FrontDoorError(Exception):
    pass


@dataclass
class FrontDoorResult:
    frontdoor_url: str          # the one-time URL to redirect the browser to


async def build_salesforce_frontdoor(
    *,
    access_token: str,
    instance_url: str,
    ret_url: str | None = None,
    timeout: float = 20.0,
) -> FrontDoorResult:
    """Call POST {instance_url}/services/oauth2/singleaccess with the access
    token; return the one-time frontdoor URL Salesforce mints.

    ret_url: optional relative path to land on inside Salesforce, e.g.
             "/lightning/o/Lead/list" or "/lightning/r/Lead/<id>/view".
             If omitted, lands on the default home.
    """
    if not access_token:
        raise FrontDoorError("no access_token provided")
    if not instance_url:
        raise FrontDoorError("no instance_url provided")

    endpoint = f"{instance_url.rstrip('/')}/services/oauth2/singleaccess"
    # The access token authorizes the singleaccess call itself.
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body: dict[str, str] = {}
    if ret_url:
        body["retURL"] = ret_url

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise FrontDoorError(f"singleaccess request failed: {e}") from e

    if resp.status_code >= 400:
        # Do NOT log the body — it can contain sensitive URL material.
        raise FrontDoorError(
            f"singleaccess returned {resp.status_code}"
        )

    try:
        data = resp.json()
    except Exception as e:
        raise FrontDoorError(f"singleaccess response not JSON: {e}") from e

    # Salesforce returns the one-time URL under "frontdoor_uri"
    # (older/newer API versions have used "url" — accept either).
    url = data.get("frontdoor_uri") or data.get("url")
    if not url:
        raise FrontDoorError(
            f"singleaccess response missing frontdoor URL; keys={list(data.keys())}"
        )

    return FrontDoorResult(frontdoor_url=url)