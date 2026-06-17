"""
Auth routes: /auth/register, /auth/login, /auth/logout, /auth/me.

Login sets an httpOnly, secure, SameSite cookie holding the raw session
token; the server stores only its hash. Logout revokes the session and
clears the cookie.
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response

from app.config import get_settings
from app.schemas.auth import (
    LoginBody, RegisterBody, User, UserPublic,
)
from app.api.deps import get_current_user, SESSION_COOKIE
from app.services.oauth.service import (
    register, login, logout, AuthError, SESSION_TTL,
)
from app.services.oauth.sql_store import get_user_store, get_session_store

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
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
async def register_route(body: RegisterBody, response: Response):
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
async def login_route(body: LoginBody, response: Response):
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