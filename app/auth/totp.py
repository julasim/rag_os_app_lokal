"""
TOTP (RFC 6238) für den MCP-Admin-Write (Track E5) — reine Standardbibliothek,
kein Extra-Dependency (air-gapped-tauglich).

Zweck: `rag_upload` über MCP ist eine schreibende Aktion. Ein `write`-Scope
allein genügt NICHT (sonst könnte ein Bearer-Key mit write schreiben). Statt-
dessen muss der EINE designierte MCP-Admin (settings().resolved_mcp_admin_email)
pro Upload einen frischen TOTP-Code liefern.

Härtung:
  * **Single-use:** ein erfolgreich genutzter Code (genauer: der getroffene
    Zeitschritt) wird verbrannt → kein Replay innerhalb seines Gültigkeitsfensters.
  * **Harter Lockout:** 5 Fehlversuche innerhalb `_LOCKOUT_WINDOW` → `_LOCKOUT_SECONDS`
    Sperre (deutlich strenger als das 10/min-Rate-Limit, das der Plan als zu lasch
    einstuft).
  * **±1 Zeitschritt** Toleranz (Uhren-Drift), 30-s-Periode, 6 Stellen.

Threat-Model (Kurz, siehe CLAUDE.md §16/§13):
  * Enrollment: Secret wird EINMAL bei `/api/auth/totp/enroll` erzeugt und dem
    Admin als otpauth-URI/Secret gezeigt; `totp_enabled` erst nach `confirm`.
  * Verlorenes Gerät / Recovery: ein Web-UI-Admin kann per `/api/auth/totp/disable`
    zurücksetzen (Session-Auth, kein TOTP nötig) → neues Enrollment.
  * ±1-Fenster: bewusst akzeptiertes Rest-Replay-Fenster von ≤90 s wird durch
    Single-use-Verbrennen des getroffenen Zeitschritts geschlossen.

Invariante (Single-Instance): Lockout-/Used-Maps sind in-memory. Bei einer
zweiten rag-api-Replica schwächen sie sich ab (wie Rate-Limits/Auth-Codes) —
siehe Masterplan „Offene Entscheidungen / Invarianten".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from urllib.parse import quote

_DIGITS = 6
_PERIOD = 30          # Sekunden pro Zeitschritt
_WINDOW = 1           # ±1 Schritt Toleranz (Uhren-Drift)

_MAX_FAILURES = 5
_LOCKOUT_WINDOW = 300.0     # Fehlversuche innerhalb dieses Fensters zählen
_LOCKOUT_SECONDS = 900.0    # Sperrdauer nach _MAX_FAILURES


# ---------------------------------------------------------------------------
# RFC 6238 / 4226 — reine Berechnung
# ---------------------------------------------------------------------------
def generate_secret() -> str:
    """Neues Base32-Secret (20 Zufallsbytes) für Authenticator-Apps."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = h[-1] & 0x0F
    truncated = struct.unpack(">I", h[off:off + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10 ** _DIGITS)).zfill(_DIGITS)


def verify(secret_b32: str, code: str, at: float | None = None) -> int | None:
    """
    Prüft `code` gegen ±_WINDOW Zeitschritte. Rückgabe: der getroffene
    Zeitschritt (counter) bei Erfolg — für die Single-use-Bindung —, sonst None.
    Konstantzeit-Vergleich gegen Timing-Leaks.
    """
    if not secret_b32 or not code or not code.isdigit() or len(code) != _DIGITS:
        return None
    base = int((at if at is not None else time.time()) // _PERIOD)
    hit: int | None = None
    for w in range(-_WINDOW, _WINDOW + 1):
        counter = base + w
        if hmac.compare_digest(_hotp(secret_b32, counter), code):
            hit = counter  # nicht früh brechen → konstante Schleifenlast
    return hit


def provisioning_uri(secret_b32: str, email: str, issuer: str = "RAG-OS") -> str:
    """otpauth://-URI für QR-Code beim Enrollment."""
    label = quote(f"{issuer}:{email}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits={_DIGITS}&period={_PERIOD}"
    )


# ---------------------------------------------------------------------------
# Lockout + Single-use (in-memory, thread-safe)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
# (user_id, counter) -> Ablaufzeitpunkt (Verbrennung, gegen Replay)
_used: dict[tuple[str, int], float] = {}
# user_id -> (Fehlversuch-Zähler, Fenster-Start)
_failures: dict[str, tuple[int, float]] = {}
# user_id -> Sperr-Ende
_locked_until: dict[str, float] = {}


def _prune(now: float) -> None:
    for k in [k for k, exp in _used.items() if exp <= now]:
        del _used[k]
    for uid in [u for u, until in _locked_until.items() if until <= now]:
        del _locked_until[uid]


def is_locked(user_id: str) -> bool:
    now = time.time()
    with _lock:
        until = _locked_until.get(user_id)
        return until is not None and until > now


def _record_failure(user_id: str, now: float) -> None:
    count, start = _failures.get(user_id, (0, now))
    if now - start >= _LOCKOUT_WINDOW:
        count, start = 0, now
    count += 1
    _failures[user_id] = (count, start)
    if count >= _MAX_FAILURES:
        _locked_until[user_id] = now + _LOCKOUT_SECONDS
        _failures.pop(user_id, None)


def check_and_consume(user_id: str, secret_b32: str, code: str) -> bool:
    """
    Verifiziert `code` für `user_id` und **verbrennt** den getroffenen Zeit-
    schritt (Single-use). Zählt Fehlversuche und sperrt hart nach _MAX_FAILURES.

    True nur, wenn: nicht gesperrt, Code gültig, Code (Zeitschritt) noch nicht
    verbraucht. Jeder andere Ausgang zählt als Fehlversuch.
    """
    now = time.time()
    with _lock:
        _prune(now)
        until = _locked_until.get(user_id)
        if until is not None and until > now:
            return False

        counter = verify(secret_b32, code, now)
        if counter is None:
            _record_failure(user_id, now)
            return False

        burn = (user_id, counter)
        if burn in _used:
            # Replay eines bereits genutzten Codes → wie Fehlversuch behandeln.
            _record_failure(user_id, now)
            return False

        # Erfolg: Zeitschritt verbrennen (bis Ende seines Toleranzfensters),
        # Fehlversuche zurücksetzen.
        _used[burn] = (counter + _WINDOW + 1) * _PERIOD
        _failures.pop(user_id, None)
        return True
