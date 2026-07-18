"""Login / Logout / Me — für die Admin-UI."""
from __future__ import annotations

import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from api.schemas import LoginRequest, LoginResponse, UserResponse
from auth import totp
from auth.dependencies import AuthContext, require_ui_admin, require_ui_user
from auth.users import authenticate_user, create_session_token
from db.models import UiUser
from db.session import get_session
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


# ---------------------------------------------------------------------------
# TOTP-Enrollment (Track E5) — für den MCP-Admin (zweiter Faktor für MCP-Write).
# Alle Endpunkte nur für Admins (require_ui_admin) und wirken auf das EIGENE Konto.
# Ablauf: enroll (Secret erzeugen, noch nicht aktiv) → im Authenticator scannen →
# confirm (Code prüfen → aktiv). disable = Reset (verlorenes Gerät).
# ---------------------------------------------------------------------------
class TotpEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TotpCodeRequest(BaseModel):
    code: str


class TotpStatusResponse(BaseModel):
    enabled: bool


@router.get("/totp/status", response_model=TotpStatusResponse)
async def totp_status(ctx: AuthContext = Depends(require_ui_admin)):
    return TotpStatusResponse(enabled=bool(ctx.ui_user.totp_enabled))


@router.post("/totp/enroll", response_model=TotpEnrollResponse)
async def totp_enroll(ctx: AuthContext = Depends(require_ui_admin)):
    """Neues Secret erzeugen (überschreibt ein evtl. vorhandenes). `totp_enabled`
    bleibt false bis zum erfolgreichen `confirm`."""
    secret = totp.generate_secret()
    async with get_session() as s:
        u = (await s.execute(select(UiUser).where(UiUser.id == ctx.ui_user.id))).scalar_one()
        u.totp_secret = secret
        u.totp_enabled = False
    log.info("auth.totp.enrolled", email=ctx.ui_user.email)
    return TotpEnrollResponse(
        secret=secret,
        provisioning_uri=totp.provisioning_uri(secret, ctx.ui_user.email),
    )


@router.post("/totp/confirm", response_model=TotpStatusResponse)
async def totp_confirm(payload: TotpCodeRequest, ctx: AuthContext = Depends(require_ui_admin)):
    """Aktiviert TOTP, wenn der gelieferte Code zum eingerichteten Secret passt."""
    async with get_session() as s:
        u = (await s.execute(select(UiUser).where(UiUser.id == ctx.ui_user.id))).scalar_one()
        if not u.totp_secret:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kein TOTP-Enrollment vorhanden")
        if totp.verify(u.totp_secret, payload.code) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code ungültig")
        u.totp_enabled = True
    log.info("auth.totp.confirmed", email=ctx.ui_user.email)
    return TotpStatusResponse(enabled=True)


@router.post("/totp/disable", response_model=TotpStatusResponse)
async def totp_disable(ctx: AuthContext = Depends(require_ui_admin)):
    """Setzt TOTP zurück (Recovery bei verlorenem Gerät). Session-Auth genügt."""
    async with get_session() as s:
        u = (await s.execute(select(UiUser).where(UiUser.id == ctx.ui_user.id))).scalar_one()
        u.totp_secret = None
        u.totp_enabled = False
    log.info("auth.totp.disabled", email=ctx.ui_user.email)
    return TotpStatusResponse(enabled=False)
