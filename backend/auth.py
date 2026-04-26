"""
auth — cchost user authentication.

Sign-in flow:
  1. Frontend hits /api/auth/login?return_url=...
  2. We redirect to the silver-oauth broker /start with scope=openid (broker
     auto-adds userinfo.email) and our /api/auth/callback as the broker's
     return_url.
  3. Broker runs Google OAuth, then redirects to our /api/auth/callback with
     ?silver_oauth=<jwt> (signed handoff JWT proving the email).
  4. We verify the JWT (using BROKER_HANDOFF_SECRET shared with the broker),
     check the email is in CCHOST_ALLOWED_EMAILS, and set a session cookie.
  5. Subsequent requests carry the cookie; middleware validates it.

Env:
  CCHOST_ALLOWED_EMAILS      comma-separated email allowlist (required for any
                             sign-in to succeed)
  CCHOST_SESSION_SECRET      HMAC key for our own session cookie
  BROKER_HANDOFF_SECRET      HMAC key shared with the broker for verifying
                             the silver_oauth=<jwt> handoff
  CCHOST_SESSION_TTL_DAYS    cookie/session lifetime (default 30)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import broker_client
import jwt
import requests
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

logger = logging.getLogger("cchost")

SESSION_COOKIE = "cchost_session"
HANDOFF_QUERY_PARAM = "silver_oauth"

# Endpoints that don't require a valid session. Anything else is gated.
PUBLIC_PATHS: set[str] = {
    "/api/auth/login",
    "/api/auth/callback",
    "/api/auth/me",  # returns 401 if no session — used by frontend to detect login state
    "/api/auth/logout",
    "/health",
    "/",
    "/ui",
}


def _session_secret() -> str:
    s = os.environ.get("CCHOST_SESSION_SECRET", "").strip()
    if not s:
        raise RuntimeError("CCHOST_SESSION_SECRET not set")
    return s


def _handoff_secret() -> str:
    s = os.environ.get("BROKER_HANDOFF_SECRET", "").strip()
    if not s:
        raise RuntimeError("BROKER_HANDOFF_SECRET not set")
    return s


def _allowed_emails() -> set[str]:
    raw = os.environ.get("CCHOST_ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _session_ttl_seconds() -> int:
    return int(os.environ.get("CCHOST_SESSION_TTL_DAYS", "30")) * 86400


def is_configured() -> bool:
    """True only if the env is fully wired for auth. If False, middleware fails closed."""
    try:
        _session_secret()
        _handoff_secret()
    except RuntimeError:
        return False
    return bool(_allowed_emails())


def make_session_cookie(email: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"email": email, "iat": now, "exp": now + _session_ttl_seconds()},
        _session_secret(),
        algorithm="HS256",
    )


def verify_session_cookie(value: str) -> Optional[str]:
    try:
        data = jwt.decode(value, _session_secret(), algorithms=["HS256"])
        return data.get("email")
    except jwt.PyJWTError as exc:
        logger.debug("session cookie verification failed: %s", exc)
        return None


def verify_handoff_jwt(token: str) -> Optional[str]:
    try:
        data = jwt.decode(token, _handoff_secret(), algorithms=["HS256"])
        return data.get("email")
    except jwt.PyJWTError as exc:
        logger.warning("handoff JWT verification failed: %s", exc)
        return None


def current_user(request: Request) -> Optional[str]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    email = verify_session_cookie(cookie)
    if not email:
        return None
    if email.lower() not in _allowed_emails():
        return None
    return email


def _set_session_cookie(response: Response, email: str, request: Request) -> None:
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(email),
        max_age=_session_ttl_seconds(),
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/api/auth/me")
def auth_me(request: Request):
    email = current_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="not signed in")
    return {"email": email}


@router.get("/api/auth/login")
def auth_login(request: Request, return_url: str = "/"):
    """Kick off sign-in via the broker. The broker will run Google OAuth and
    redirect back to /api/auth/callback with a signed handoff JWT."""
    if not is_configured():
        raise HTTPException(
            status_code=500,
            detail="cchost auth not configured (CCHOST_ALLOWED_EMAILS / CCHOST_SESSION_SECRET / BROKER_HANDOFF_SECRET)",
        )
    # Build our own callback URL pointing back at this host.
    host = request.headers.get("host", "")
    scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else request.url.scheme
    our_callback = f"{scheme}://{host}/api/auth/callback?return={requests.utils.quote(return_url, safe='')}"
    try:
        broker_url = broker_client.start_url(our_callback, "openid")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"broker not configured: {exc}")
    return RedirectResponse(url=broker_url)


@router.get("/api/auth/callback")
def auth_callback(request: Request):
    """Verify the handoff JWT from the broker, set our own session cookie, and bounce to return_url."""
    # The broker sends `?return=...&silver_oauth=...`. We read both from the
    # request directly because `return` is a Python keyword.
    return_target = request.query_params.get("return") or "/"
    token = request.query_params.get(HANDOFF_QUERY_PARAM, "")
    if not token:
        raise HTTPException(status_code=400, detail="missing silver_oauth handoff token")
    email = verify_handoff_jwt(token)
    if not email:
        raise HTTPException(status_code=401, detail="handoff token invalid or expired")
    if email.lower() not in _allowed_emails():
        logger.info("rejected sign-in for non-allowlisted email: %s", email)
        raise HTTPException(status_code=403, detail=f"{email} is not allowed to use this app")
    response = RedirectResponse(url=return_target)
    _set_session_cookie(response, email, request)
    return response


@router.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"ok": True})
    _clear_session_cookie(response)
    return response


@router.post("/api/auth/verify-handoff")
def verify_handoff(request: Request, payload: dict):
    """Verify a broker handoff JWT and return the email it asserts.

    Used by the Gmail-connect flow: the broker hands the user back to the
    frontend with `?silver_oauth=<jwt>`; the frontend can't verify it (no
    secret), so it POSTs the token here and we tell it the email.

    Requires a valid cchost session — only signed-in users can associate a
    Gmail account.
    """
    if not current_user(request):
        raise HTTPException(status_code=401, detail="not signed in")
    token = (payload or {}).get("token", "")
    if not token:
        raise HTTPException(status_code=400, detail="token required")
    email = verify_handoff_jwt(token)
    if not email:
        raise HTTPException(status_code=401, detail="handoff token invalid or expired")
    return {"email": email}
