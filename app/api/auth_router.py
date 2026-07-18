"""Login / Logout / Me — für die Admin-UI."""
from __future__ import annotations

import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.schemas import LoginRequest, LoginResponse, UserResponse
from auth.dependencies import AuthContext, require_ui_user
from auth.users import authenticate_user, create_session_token
from logger import log

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Login-Rate-Limit gegen Passwort-Brute-Force.
#
# Ohne dies kann ein Angreifer beliebig viele Passwörter pro Sekunde
# durchprobieren (im Audit nachgewiesen: 60 Versuche → 0 Ablehnungen).
# Wir drosseln PRO E-MAIL (nicht pro IP): hinter dem Edge-Proxy teilen sich
# alle Clients dieselbe Quell-IP, und die E-Mail ist der unspoofbare
# Angriffs-Zielschlüssel. So wird das Raten eines konkreten Kontos begrenzt,
# ohne legitime Nutzer anderer Konten auszusperren.
# ---------------------------------------------------------------------------
_LOGIN_MAX = int(os.environ.get("LOGIN_RATE_LIMIT_PER_MIN", "10"))
_LOGIN_WINDOW = 60.0
_login_buckets: dict[str, tuple[float, float]] = {}
_login_lock = threading.Lock()


_LOGIN_PRUNE_THRESHOLD = 5_000


def _login_allowed(email: str) -> bool:
    if _LOGIN_MAX <= 0:
        return True
    key = email.strip().lower()
    now = time.monotonic()
    with _login_lock:
        if len(_login_buckets) > _LOGIN_PRUNE_THRESHOLD:
            for k in [k for k, (_, st) in _login_buckets.items() if now - st >= _LOGIN_WINDOW]:
                del _login_buckets[k]
        remaining, start = _login_buckets.get(key, (_LOGIN_MAX, now))
        if now - start >= _LOGIN_WINDOW:
            remaining, start = _LOGIN_MAX, now
        if remaining < 1:
            _login_buckets[key] = (remaining, start)
            return False
        _login_buckets[key] = (remaining - 1, start)
        return True


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, response: Response):
    if not _login_allowed(payload.email):
        log.warning("auth.login.rate_limited", email=payload.email)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Anmeldeversuche. Bitte kurz warten.",
            headers={"Retry-After": str(int(_LOGIN_WINDOW))},
        )
    user = await authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token = create_session_token(user)
    # Session-Cookie für Browser-Navigation
    response.set_cookie(
        "rag_session",
        token,
        httponly=True,
        samesite="lax",
        secure=True,
        max_age=12 * 3600,
    )
    return LoginResponse(
        token=token,
        user=UserResponse(id=user.id, email=user.email, role=user.role),
    )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("rag_session")
    return {"success": True}


@router.get("/me", response_model=UserResponse)
async def me(ctx: AuthContext = Depends(require_ui_user)):
    u = ctx.ui_user
    return UserResponse(id=u.id, email=u.email, role=u.role)
