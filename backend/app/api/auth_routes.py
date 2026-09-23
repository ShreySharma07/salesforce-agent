"""
Auth routes: /auth/register, /auth/login, /auth/logout, /auth/me.

Login sets an httpOnly, secure, SameSite cookie holding the raw session
token; the server stores only its hash. Logout revokes the session and
clears the cookie.
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from app.config import get_settings
from app.schemas.auth import (
    LoginBody, RegisterBody, User, UserPublic,
)
from app.api.deps import get_current_user, SESSION_COOKIE
from app.services.oauth.service import (
    register, login, logout, AuthError, SESSION_TTL,
)
from app.services.oauth.sql_store import get_user_store, get_session_store
from app.services.rate_limit import AuthRateLimits

router = APIRouter(prefix="/auth", tags=["auth"])


def _enforce_rate_limit(request: Request, email: str) -> None:
    """429 once a client IP or a target email exceeds its attempt budget."""
    limits: AuthRateLimits | None = getattr(request.app.state, "auth_rate_limits", None)
    if limits is None:
        limits = request.app.state.auth_rate_limits = AuthRateLimits()
    ip = request.client.host if request.client else "unknown"
    ok_ip = limits.per_ip.hit(f"ip:{ip}")
    ok_email = limits.per_email.hit(f"email:{email.lower()}")
    if not (ok_ip and ok_email):
        raise HTTPException(429, "too many attempts; try again later")


def _set_session_cookie(response: Response, token: str) -> None:
    """Attach the raw session token as an httpOnly cookie (secure only outside dev mode)."""
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=not getattr(settings, "auth_dev_mode", False),  # allow http in dev
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


@router.post("/register", response_model=UserPublic)
async def register_route(body: RegisterBody, request: Request, response: Response):
    _enforce_rate_limit(request, body.email)
    try:
        user = await register(
            get_user_store(), email=body.email, password=body.password,
            display_name=body.display_name,
        )
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Auto-login on register.
    _, token = await login(
        get_user_store(), get_session_store(),
        email=body.email, password=body.password,
    )
    _set_session_cookie(response, token)
    return UserPublic(id=user.id, email=user.email,
                      display_name=user.display_name,
                      organization_id=user.organization_id)


@router.post("/login", response_model=UserPublic)
async def login_route(body: LoginBody, request: Request, response: Response):
    _enforce_rate_limit(request, body.email)
    try:
        user, token = await login(
            get_user_store(), get_session_store(),
            email=body.email, password=body.password,
        )
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    _set_session_cookie(response, token)
    return UserPublic(id=user.id, email=user.email,
                      display_name=user.display_name,
                      organization_id=user.organization_id)


@router.post("/logout")
async def logout_route(response: Response,
                       session: str | None = Cookie(default=None)):
    if session:
        await logout(get_session_store(), session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
async def me_route(user: User = Depends(get_current_user)):
    return UserPublic(id=user.id, email=user.email,
                      display_name=user.display_name,
                      organization_id=user.organization_id)