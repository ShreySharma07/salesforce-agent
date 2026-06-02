"""
Specific OAuth provider configurations.

Each entry plugs into the generic OAuth flow in base.py. To add a new
provider:
  1. Find its OAuth 2.0 endpoints (authorize URL + token URL)
  2. Decide what scopes you need
  3. Add an entry to PROVIDERS below
  4. Add SOMETHING_CLIENT_ID + SOMETHING_CLIENT_SECRET to config.py
  5. Register the redirect URI in the provider's developer console:
       {PUBLIC_BACKEND_BASE_URL}/oauth/{name}/callback
"""
from __future__ import annotations

from app.config import get_settings
from app.services.oauth.base import OAuthProvider


def _salesforce() -> OAuthProvider:
    """Salesforce Connected App OAuth 2.0 Web Server Flow.
    https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_web_server_flow.htm
    """
    settings = get_settings()
    base = settings.salesforce_auth_url.rstrip("/")
    return OAuthProvider(
        name="salesforce",
        authorize_url=f"{base}/services/oauth2/authorize",
        token_url=f"{base}/services/oauth2/token",
        scopes=["api", "web", "refresh_token", "offline_access"],
        use_pkce=False,                      # supported but optional
        extra_authorize_params={
            "prompt": "login consent",       # always show approval screen — easy demo
        },
        public_metadata_keys=["instance_url", "id", "signature"],
        # Salesforce returns issued_at (ms timestamp) instead of expires_in.
        # Connected App sessions default to 2 hours; use that as the fallback.
        token_lifetime_seconds=7200,
    )


def _google() -> OAuthProvider:
    """Google OAuth 2.0 (Gmail, Drive, etc).
    https://developers.google.com/identity/protocols/oauth2/web-server
    """
    return OAuthProvider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "openid", "email", "profile",
        ],
        use_pkce=True,                       # Google requires PKCE for new apps
        extra_authorize_params={
            "access_type": "offline",        # required to get a refresh_token
            "prompt": "consent",             # forces refresh_token to be returned every time
        },
        public_metadata_keys=["id_token"],
    )


def _slack() -> OAuthProvider:
    """Slack OAuth 2.0 (v2 / "granular bot scopes").
    https://api.slack.com/authentication/oauth-v2
    """
    return OAuthProvider(
        name="slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        scopes=["chat:write", "channels:read", "users:read"],
        use_pkce=False,
        public_metadata_keys=["team", "authed_user", "scope"],
    )


# Provider registry, lowercase name → instance.
def get_provider(name: str) -> OAuthProvider:
    name = name.lower()
    if name == "salesforce":
        return _salesforce()
    if name == "google":
        return _google()
    if name == "slack":
        return _slack()
    raise KeyError(f"unknown OAuth provider: {name!r}. "
                   "Register it in app/services/oauth/providers.py")


def get_provider_credentials(name: str) -> tuple[str | None, str | None]:
    """Returns (client_id, client_secret) from settings, or (None, None) if unconfigured."""
    s = get_settings()
    name = name.lower()
    if name == "salesforce":
        return s.salesforce_client_id, s.salesforce_client_secret
    if name == "google":
        return s.google_client_id, s.google_client_secret
    if name == "slack":
        return s.slack_client_id, s.slack_client_secret
    return None, None


def list_provider_names() -> list[str]:
    return ["salesforce", "google", "slack"]