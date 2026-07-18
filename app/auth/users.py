"""
Admin-UI-Benutzer: Passwörter via bcrypt, JWT-Sessions.

Einfach, robust, ohne OAuth-Overhead.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select

from config import settings
from db.models import UiUser, UserRole
from db.session import get_session
from logger import log

_JWT_ALG = "HS256"
_JWT_EXPIRE_HOURS = 12

# Konstanter Dummy-Hash: wird bei unbekannter E-Mail gegengeprüft, damit die
# Antwortzeit nicht verrät, ob ein Konto existiert (User-Enumeration).
_DUMMY_HASH = bcrypt.hashpw(b"timing-attack-mitigation", bcrypt.gensalt()).decode("utf-8")


def hash_password(password: str) -> str:
    """bcrypt-Hash für ein Klartext-Passwort (zentral, damit alle Pfade — Bootstrap,
    User-Anlage, Passwort-Änderung — dieselbe Kostenfunktion nutzen)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# ---------------------------------------------------------------------------
# Bootstrap: Admin-User anlegen, falls noch keiner existiert
# ---------------------------------------------------------------------------
async def ensure_admin_user() -> None:
    async with get_session() as s:
        existing = await s.execute(
            select(UiUser).where(UiUser.email == settings().admin_email)
        )
        if existing.scalar_one_or_none():
            return

        pw_hash = bcrypt.hashpw(
            settings().admin_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        s.add(
            UiUser(
                email=settings().admin_email,
                password_hash=pw_hash,
                role=UserRole.ADMIN.value,
                access_all=True,  # Bootstrap-Admin = Vollzugriff (explizit, nicht Default)
            )
        )
        log.info("auth.admin.created", email=settings().admin_email)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
async def authenticate_user(email: str, password: str) -> UiUser | None:
    async with get_session() as s:
        result = await s.execute(select(UiUser).where(UiUser.email == email))
        user = result.scalar_one_or_none()
        if not user:
            # Dummy-Check gegen Konstant-Hash → gleiche Antwortzeit wie bei
            # existierendem Konto mit falschem Passwort.
            bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH.encode("utf-8"))
            return None
        if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
            return None
        user.last_login = datetime.now(timezone.utc)
        return user


def create_session_token(user: UiUser) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings().app_secret_key, algorithm=_JWT_ALG)


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings().app_secret_key, algorithms=[_JWT_ALG])
    except JWTError:
        return None
