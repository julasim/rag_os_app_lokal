"""
OAuth 2.1 Routen + Well-Known-Metadaten.

Diese Routen werden OHNE Auth-Middleware direkt in die FastAPI-App
eingebunden (vor dem MCP-Mount), damit Claude.ai sie für die
Auth-Discovery erreicht.

Routen:
  GET  /.well-known/oauth-protected-resource   RFC 9728
  GET  /.well-known/oauth-authorization-server RFC 8414
  POST /oauth/register                          RFC 7591
  GET  /oauth/authorize                         Login-Formular
  POST /oauth/authorize                         Auth-Code ausstellen
  POST /oauth/token                             Token-Austausch
  POST /oauth/revoke                            RFC 7009

Übernommen und angepasst aus julasim/MCP-Template.
"""
from __future__ import annotations

import html
import urllib.parse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth.ratelimit import RateLimiter
from auth.users import authenticate_user
from config import settings
from logger import log

from . import oauth

router = APIRouter(tags=["mcp-oauth"])

# Rate-Limits — die /oauth/*-Routen laufen NICHT durch die MCP-Rate-Limit-
# Middleware (die umschließt nur den /mcp-Stack), also hier explizit:
#   Login pro E-Mail (Passwort-Brute-Force), Token pro client_id, DCR pro IP.
_login_rl = RateLimiter(max_per_window=10)      # 10 Login-Versuche / min / E-Mail
_token_rl = RateLimiter(max_per_window=60)      # 60 Token-Requests / min / client
_register_rl = RateLimiter(max_per_window=20)   # 20 DCR / min / IP (grob, + MAX_CLIENTS)
_TOO_MANY = {"error": "rate_limited"}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

# Content-Security-Policy für die Login-Seite: erlaubt nur das inline-<style>,
# kein Skript/keine externen Ressourcen — zweite Verteidigungslinie gegen XSS.
_LOGIN_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"


def _render_login(*, client_id: str, redirect_uri: str, state: str,
                  code_challenge: str, scope: str, error: str = "",
                  status_code: int = 200) -> HTMLResponse:
    """Rendert die Login-Seite mit HTML-escapten Werten (reflektiertes XSS)."""
    page = _LOGIN_HTML.format(
        client_id=html.escape(client_id, quote=True),
        redirect_uri=html.escape(redirect_uri, quote=True),
        state=html.escape(state, quote=True),
        code_challenge=html.escape(code_challenge, quote=True),
        scope=html.escape(scope, quote=True),
        error=error,  # nur intern gesetzte, feste Strings (siehe _err)
    )
    return HTMLResponse(page, status_code=status_code,
                        headers={"Content-Security-Policy": _LOGIN_CSP})

# Erlaubte OAuth-Scopes. Der angeforderte Scope wird hiergegen gefiltert —
# sonst würde ein Client mit `scope=admin` (o.Ä.) genau diesen Scope ins Token
# geschrieben bekommen (keine Prüfung = Privilege-Escalation-Fläche).
_ALLOWED_SCOPES = {"mcp"}
_DEFAULT_SCOPE = "mcp"


def _clamp_scope(requested: str) -> str:
    """Behält nur erlaubte Scope-Tokens; fällt auf den Default zurück."""
    keep = [t for t in (requested or "").split() if t in _ALLOWED_SCOPES]
    return " ".join(keep) if keep else _DEFAULT_SCOPE

# ---------------------------------------------------------------------------
# Well-Known-Endpunkte (immer erreichbar, auch wenn OAuth deaktiviert)
# ---------------------------------------------------------------------------

@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def well_known_resource(request: Request):
    """RFC 9728 — beschreibt diesen geschützten MCP-Endpunkt.

    Issuer/Resource kommen aus settings() (nicht request.base_url) → sie stimmen
    exakt mit `iss`/`aud` im ausgestellten Token überein (sonst Client-Reject).
    """
    cfg = settings()
    return JSONResponse({
        "resource": cfg.oauth_resource,
        "authorization_servers": [cfg.oauth_issuer] if oauth.is_enabled() else [],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{cfg.oauth_issuer}/docs",
    })


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def well_known_server(request: Request):
    """RFC 8414 — beschreibt den OAuth-Authorization-Server."""
    if not oauth.is_enabled():
        return JSONResponse({"error": "oauth_disabled"}, status_code=404)
    base = settings().oauth_issuer
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------

@router.post("/oauth/register", include_in_schema=False)
async def oauth_register(request: Request):
    if not oauth.is_enabled():
        return JSONResponse({"error": "oauth_disabled"}, status_code=400)
    if not _register_rl.allow(_client_ip(request)):
        return JSONResponse(_TOO_MANY, status_code=429, headers={"Retry-After": "60"})
    body = await request.json()
    redirect_uris = body.get("redirect_uris", [])
    if not redirect_uris or not isinstance(redirect_uris, list):
        return JSONResponse({"error": "redirect_uris required"}, status_code=400)
    # DCR-Spam-Deckel (unauthentifizierter Endpunkt).
    if await oauth.count_clients() >= oauth.MAX_CLIENTS:
        log.warning("oauth.register_capped")
        return JSONResponse({"error": "client_limit_reached"}, status_code=429)
    client = await oauth.register_client(redirect_uris)
    return JSONResponse({
        "client_id": client.client_id,
        "redirect_uris": client.redirect_uris,
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
    }, status_code=201)


# RFC 7591 Alias: Claude.ai ruft POST /register (ohne /oauth/-Prefix)
@router.post("/register", include_in_schema=False)
async def register_alias(request: Request):
    return await oauth_register(request)


# ---------------------------------------------------------------------------
# Authorization-Endpunkt
# ---------------------------------------------------------------------------

_LOGIN_HTML = """\
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAG OS — MCP Anmeldung</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,"Inter",sans-serif;background:#fafafa;display:flex;
        align-items:center;justify-content:center;min-height:100vh;padding:16px}}
  .card{{background:#fff;border:1px solid #ededed;border-radius:10px;padding:32px 28px;
         width:100%;max-width:380px}}
  h1{{font-size:18px;font-weight:600;color:#111;margin-bottom:4px}}
  .sub{{font-size:13px;color:#737373;margin-bottom:24px}}
  label{{display:block;font-size:12px;font-weight:500;color:#525252;margin-bottom:4px}}
  input{{width:100%;padding:8px 10px;border:1px solid #ededed;border-radius:6px;
         font-size:14px;color:#111;outline:none;margin-bottom:14px}}
  input:focus{{border-color:#111}}
  button{{width:100%;padding:9px;background:#111;color:#fff;border:none;
           border-radius:6px;font-size:14px;font-weight:500;cursor:pointer;margin-top:4px}}
  button:hover{{background:#262626}}
  .err{{color:#991b1b;font-size:13px;margin-bottom:12px}}
  .meta{{font-size:11px;color:#a3a3a3;margin-top:16px;word-break:break-all}}
</style>
</head><body><div class="card">
<h1>RAG OS</h1>
<p class="sub">MCP-Server Anmeldung</p>
{error}
<form method="post">
<input type="hidden" name="client_id" value="{client_id}">
<input type="hidden" name="redirect_uri" value="{redirect_uri}">
<input type="hidden" name="state" value="{state}">
<input type="hidden" name="code_challenge" value="{code_challenge}">
<input type="hidden" name="scope" value="{scope}">
<label>E-Mail</label>
<input type="email" name="email" autocomplete="email" required>
<label>Passwort</label>
<input type="password" name="password" autocomplete="current-password" required>
<button type="submit">Anmelden</button>
</form>
<p class="meta">Client: {client_id}<br>Redirect: {redirect_uri}</p>
</div></body></html>
"""


@router.get("/oauth/authorize", include_in_schema=False)
async def oauth_authorize_get(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    response_type: str = "code",
    scope: str = "mcp",
):
    if not oauth.is_enabled():
        return JSONResponse({"error": "oauth_disabled"}, status_code=400)
    if response_type != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if code_challenge_method != "S256":
        return JSONResponse({"error": "unsupported_code_challenge_method"}, status_code=400)
    if not code_challenge:
        return JSONResponse({"error": "code_challenge required"}, status_code=400)
    client = await oauth.get_client(client_id)
    if not client:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    # redirect_uri MUSS zu den registrierten URIs des Clients gehören — sonst
    # Auth-Code-Diebstahl per Phishing (angreiferkontrolliertes Redirect-Ziel).
    if not oauth._validate_redirect_uri(client, redirect_uri):
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    return _render_login(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
        scope=scope,
    )


@router.post("/oauth/authorize", include_in_schema=False)
async def oauth_authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    code_challenge: str = Form(...),
    scope: str = Form("mcp"),
    email: str = Form(...),
    password: str = Form(...),
):
    if not oauth.is_enabled():
        return JSONResponse({"error": "oauth_disabled"}, status_code=400)

    def _err(msg: str):
        # msg ist immer ein fester interner String (kein User-Input) → sicher.
        return _render_login(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            scope=scope,
            error=f'<p class="err">{msg}</p>',
            status_code=401,
        )

    client = await oauth.get_client(client_id)
    if not client:
        return _err("Unbekannter Client.")
    # redirect_uri gegen die registrierten URIs prüfen (siehe GET-Handler).
    if not oauth._validate_redirect_uri(client, redirect_uri):
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    # Brute-Force-Schutz PRO E-MAIL (vor dem teuren bcrypt).
    if not _login_rl.allow(email.strip().lower()):
        log.warning("oauth.login_rate_limited", email=email, client_id=client_id)
        return _render_login(
            client_id=client_id, redirect_uri=redirect_uri, state=state,
            code_challenge=code_challenge, scope=scope,
            error='<p class="err">Zu viele Versuche. Bitte kurz warten.</p>',
            status_code=429,
        )

    # Login gegen die echten UiUser-Accounts (gleiche Identität wie die Admin-UI).
    # authenticate_user hat Dummy-Hash-Timing gegen User-Enumeration (S8).
    user = await authenticate_user(email, password)
    if not user:
        log.warning("oauth.login_fail", email=email, client_id=client_id)
        return _err("E-Mail oder Passwort falsch.")
    log.info("oauth.login_ok", user_id=str(user.id), client_id=client_id)

    code = oauth.issue_auth_code(
        client_id, redirect_uri, code_challenge, _clamp_scope(scope), str(user.id)
    )
    params = {"code": code}
    if state:
        params["state"] = state
    location = redirect_uri + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(location, status_code=302)


# ---------------------------------------------------------------------------
# Token-Endpunkt
# ---------------------------------------------------------------------------

@router.post("/oauth/token", include_in_schema=False)
async def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    code: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
):
    if not oauth.is_enabled():
        return JSONResponse({"error": "oauth_disabled"}, status_code=400)
    if not _token_rl.allow(client_id or _client_ip(request)):
        return JSONResponse(_TOO_MANY, status_code=429, headers={"Retry-After": "60"})

    ttl = settings().oauth_access_ttl

    if grant_type == "authorization_code":
        claims = oauth.consume_auth_code(code, client_id, redirect_uri, code_verifier)
        if not claims:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        access = oauth.issue_access_token(claims["subject"], client_id, claims["scope"])
        refresh = await oauth.issue_refresh_token(claims["subject"], client_id, claims["scope"])
        log.info("oauth.token_issued", subject=claims["subject"], client_id=client_id, grant="code")
        return JSONResponse({
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ttl,
            "refresh_token": refresh,
            "scope": claims["scope"],
        })

    elif grant_type == "refresh_token":
        result = await oauth.rotate_refresh_token(refresh_token, client_id)
        if not result:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        access = oauth.issue_access_token(result["subject"], client_id, result["scope"])
        return JSONResponse({
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ttl,
            "refresh_token": result["new_token"],
            "scope": result["scope"],
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ---------------------------------------------------------------------------
# Revocation-Endpunkt (RFC 7009)
# ---------------------------------------------------------------------------

@router.post("/oauth/revoke", include_in_schema=False)
async def oauth_revoke(token: str = Form(...)):
    if not oauth.is_enabled():
        return JSONResponse({"error": "oauth_disabled"}, status_code=400)
    await oauth.revoke_token(token)
    return JSONResponse({}, status_code=200)
