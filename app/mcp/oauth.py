"""
OAuth 2.1 authorization server for the Lextor MCP endpoint (/mcp).

This is how Claude, ChatGPT, Gemini, Cursor, VS Code… connect with ONE generic URL: the MCP
endpoint answers 401 with a pointer to these metadata, the assistant registers itself, opens
Lextor in the browser so the user logs in and approves, and then calls /mcp with a bearer token.

  /.well-known/oauth-protected-resource[/mcp]   resource metadata (RFC 9728)
  /.well-known/oauth-authorization-server       authorization server metadata (RFC 8414)
  /.well-known/openid-configuration             same document (fallback some clients probe)
  /oauth/register        dynamic client registration (RFC 7591, DCR)
  /oauth/authorize       validates the request and sends the user to the consent screen
  /oauth/consent/{id}    consent screen (needs a Lextor / naux session)
  /oauth/token           authorization_code (PKCE S256 mandatory) and refresh_token (rotating)
  /oauth/revoke          revocation (RFC 7009)

Clients are registered by DCR or identified by a Client ID Metadata Document (CIMD: the client_id is
an https URL to a JSON document, fetched with SSRF protection). CIMD clients must accept the 'none'
token auth method (public clients with PKCE); Claude and ChatGPT do.

Scopes: lextor:read and lextor:write; the user decides on the consent screen whether the assistant
may write. Tokens are opaque and only their SHA-256 is stored: access 1 h, refresh 60 days with
rotation; replaying a rotated refresh token (after a short grace period) revokes the whole grant.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import secrets
import socket
import time
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.services.storage import LOCK, read_json, write_json, system_dir

log = logging.getLogger("lextor.oauth")
router = APIRouter(tags=["oauth"])

SCOPE_READ = "lextor:read"
SCOPE_WRITE = "lextor:write"
ALL_SCOPES = [SCOPE_READ, SCOPE_WRITE]
TTL = {"request": 15 * 60, "code": 120, "access": 3600, "refresh": 60 * 86400,
       "unused_client": 7 * 86400, "cimd": 86400, "refresh_grace": 60}
KNOWN_HOSTS = {"claude.ai": "Claude", "claude.com": "Claude", "chatgpt.com": "ChatGPT",
               "chat.openai.com": "ChatGPT", "platform.openai.com": "OpenAI", "gemini.google.com": "Gemini"}
LOOPBACK = {"localhost", "127.0.0.1", "[::1]", "::1"}
FORBIDDEN_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob", "ftp", "ws", "wss"}


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


# ─────────────────────────────────────────────────────────────── storage ──

def _now() -> int:
    return int(time.time())


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _path():
    return system_dir() / "oauth.json"


def _load() -> dict:
    d = read_json(_path(), {}) or {}
    for k in ("clients", "requests", "codes", "grants", "access", "refresh"):
        d.setdefault(k, {})
    now = _now()
    for k in ("clients", "requests", "codes", "access", "refresh"):
        d[k] = {i: v for i, v in d[k].items() if not v.get("exp") or v["exp"] > now}
    return d


def _save(d: dict) -> None:
    write_json(_path(), d)


def iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# ───────────────────────────────────────────────────────── redirect URIs ──

def valid_redirect_uri(raw) -> bool:
    if not isinstance(raw, str) or len(raw) > 500:
        return False
    u = urllib.parse.urlsplit(raw)
    if u.fragment or u.username or u.password or not u.scheme:
        return False
    if u.scheme == "https":
        return bool(u.hostname)
    if u.scheme == "http":
        return (u.hostname or "") in LOOPBACK
    return u.scheme not in FORBIDDEN_SCHEMES  # native app custom scheme


def match_redirect(registered: list[str], given: Optional[str]) -> Optional[str]:
    if not given:
        return registered[0] if len(registered) == 1 else None
    if given in registered:
        return given
    g = urllib.parse.urlsplit(given)
    if g.scheme != "http" or (g.hostname or "") not in LOOPBACK:
        return None
    for r in registered:  # loopback: any port (RFC 8252 §7.3)
        u = urllib.parse.urlsplit(r)
        if u.scheme == "http" and u.hostname == g.hostname and u.path == g.path and u.query == g.query:
            return given
    return None


def _host_of(uri: str) -> str:
    u = urllib.parse.urlsplit(uri)
    return u.netloc if u.scheme in ("http", "https") else f"{u.scheme}://"


# ──────────────────────────────────────────────── Client ID Metadata Docs ──

def is_cimd_client_id(cid: str) -> bool:
    try:
        u = urllib.parse.urlsplit(cid)
    except ValueError:
        return False
    if u.scheme != "https" or not u.hostname or u.path in ("", "/") or u.fragment or u.username or u.password:
        return False
    try:
        ipaddress.ip_address(u.hostname.strip("[]"))
        return False
    except ValueError:
        return True


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified):
            return False
    return True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _fetch_json(url: str) -> dict:
    host = urllib.parse.urlsplit(url).hostname or ""
    if not _public_host(host):
        raise ValueError("dirección no pública")
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Lextor-OAuth/1"})
    with opener.open(req, timeout=6) as r:
        if r.status != 200:
            raise ValueError(f"HTTP {r.status}")
        body = r.read(64 * 1024 + 1)
    if len(body) > 64 * 1024:
        raise ValueError("documento demasiado grande")
    return json.loads(body.decode("utf-8"))


cimd_fetcher = _fetch_json  # replaced in tests


def _load_cimd(cid: str) -> dict:
    d = _load()
    if cid in d["clients"]:
        return d["clients"][cid]
    try:
        doc = cimd_fetcher(cid)
    except Exception as e:
        raise OAuthError("invalid_client", f"No se pudo leer el documento del cliente: {e}")
    if not isinstance(doc, dict) or doc.get("client_id") != cid:
        raise OAuthError("invalid_client", "El documento del cliente no corresponde a su client_id")
    uris = [u for u in (doc.get("redirect_uris") or []) if valid_redirect_uri(u)][:20]
    if not uris:
        raise OAuthError("invalid_client", "El cliente no declara direcciones de vuelta válidas")
    declared = doc.get("token_endpoint_auth_method") or "none"
    alternatives = doc.get("token_endpoint_auth_methods_supported") or []
    if declared != "none" and "none" not in alternatives:
        raise OAuthError("invalid_client", f"Método de autenticación no admitido: {declared}")
    client = {"client_id": cid, "kind": "cimd", "auth_method": "none", "redirect_uris": uris,
              "name": str(doc.get("client_name") or urllib.parse.urlsplit(cid).netloc)[:100],
              "uri": str(doc.get("client_uri") or "")[:300] or None,
              "created_at": _now(), "exp": _now() + TTL["cimd"]}
    with LOCK:
        d = _load()
        d["clients"][cid] = client
        _save(d)
    return client


def resolve_client(cid: Optional[str]) -> Optional[dict]:
    if not cid or len(cid) > 500:
        return None
    if is_cimd_client_id(cid):
        return _load_cimd(cid)
    return _load()["clients"].get(cid)


def describe_client(client: dict, redirect_uri: str) -> dict:
    redirect_host = _host_of(redirect_uri)
    loopback = (urllib.parse.urlsplit(redirect_uri).hostname or "") in LOOPBACK
    cimd_host = urllib.parse.urlsplit(client["client_id"]).netloc if client["kind"] == "cimd" else None
    verified = None
    if cimd_host:
        verified = KNOWN_HOSTS.get(cimd_host) or ("Claude" if cimd_host.endswith(".anthropic.com") else None)
    if not verified and not loopback:
        verified = KNOWN_HOSTS.get(redirect_host)
    return {"name": client["name"], "uri": client.get("uri"), "kind": client["kind"], "client_host": cimd_host,
            "redirect_host": redirect_host, "loopback": loopback, "verified_as": verified}


def requested_scopes(scope: Optional[str]) -> list[str]:
    asked = [s for s in (scope or "").split() if s in ALL_SCOPES]
    return sorted(set([SCOPE_READ, *asked])) if asked else list(ALL_SCOPES)


# ───────────────────────────────────────────────────────────── bearer ──

def verify_bearer(token: str) -> Optional[dict]:
    """Return {user_dir, user, scopes, grant_id, client} for a valid OAuth access token."""
    if not token or len(token) > 300:
        return None
    d = _load()
    t = d["access"].get(_hash(token))
    if not t:
        return None
    grant = d["grants"].get(t["grant_id"])
    if not grant:
        return None
    if _now() - grant.get("last_used_at", 0) > 300:  # throttle writes
        with LOCK:
            d = _load()
            if t["grant_id"] in d["grants"]:
                d["grants"][t["grant_id"]]["last_used_at"] = _now()
                _save(d)
    return {"user_dir": grant["user_dir"], "user": grant.get("user"), "scopes": t["scopes"],
            "grant_id": grant["id"], "client": grant.get("client_name")}


def _issue(d: dict, grant: dict) -> dict:
    access, refresh = "lxa_" + secrets.token_urlsafe(32), "lxr_" + secrets.token_urlsafe(32)
    d["access"][_hash(access)] = {"grant_id": grant["id"], "client_id": grant["client_id"],
                                  "scopes": grant["scopes"], "exp": _now() + TTL["access"]}
    d["refresh"][_hash(refresh)] = {"grant_id": grant["id"], "client_id": grant["client_id"],
                                    "exp": _now() + TTL["refresh"], "rotated_at": None}
    return {"access_token": access, "token_type": "Bearer", "expires_in": TTL["access"],
            "refresh_token": refresh, "scope": " ".join(grant["scopes"])}


def _delete_grant(d: dict, gid: str) -> None:
    d["grants"].pop(gid, None)
    for k in ("access", "refresh"):
        d[k] = {h: v for h, v in d[k].items() if v.get("grant_id") != gid}


def list_grants(user_dir: str) -> list[dict]:
    gs = [g for g in _load()["grants"].values() if g["user_dir"] == user_dir]
    return sorted(gs, key=lambda g: g.get("last_used_at", 0), reverse=True)


def revoke_grant(user_dir: str, gid: str) -> bool:
    with LOCK:
        d = _load()
        g = d["grants"].get(gid)
        if not g or g["user_dir"] != user_dir:
            return False
        _delete_grant(d, gid)
        _save(d)
    return True


# ─────────────────────────────────────────────────────────── routes ──

def _base(request: Request) -> str:
    from app.mcp.http import public_base_url
    return public_base_url(request)


def resource_url(request: Request) -> str:
    return _base(request) + "/mcp"


def resource_metadata_url(request: Request) -> str:
    return _base(request) + "/.well-known/oauth-protected-resource/mcp"


def challenge_header(request: Request, error: Optional[str] = None) -> str:
    parts = [f'resource_metadata="{resource_metadata_url(request)}"', f'scope="{" ".join(ALL_SCOPES)}"']
    if error:
        parts += [f'error="{error}"', 'error_description="The access token is invalid or expired"']
    return "Bearer " + ", ".join(parts)


def _valid_resource(request: Request, r: str) -> bool:
    b = _base(request)
    return r in (b + "/mcp", b + "/mcp/", b, b + "/")


def _cors(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Expose-Headers"] = "WWW-Authenticate"
    return resp


def _json(data, status: int = 200, no_store: bool = False) -> JSONResponse:
    r = JSONResponse(data, status_code=status)
    if no_store:
        r.headers["Cache-Control"] = "no-store"
        r.headers["Pragma"] = "no-cache"
    return _cors(r)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource(request: Request):
    b = _base(request)
    return _json({"resource": b + "/mcp", "authorization_servers": [b], "scopes_supported": ALL_SCOPES,
                  "bearer_methods_supported": ["header"], "resource_name": "Lextor",
                  "resource_documentation": b + "/connect"})


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
async def authorization_server(request: Request):
    b = _base(request)
    return _json({
        "issuer": b,
        "authorization_endpoint": b + "/oauth/authorize",
        "token_endpoint": b + "/oauth/token",
        "registration_endpoint": b + "/oauth/register",
        "revocation_endpoint": b + "/oauth/revoke",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": ALL_SCOPES + ["offline_access"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": b + "/connect",
    })


@router.options("/oauth/{path:path}")
@router.options("/.well-known/{path:path}")
async def preflight(path: str):
    r = Response(status_code=204)
    r.headers.update({"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                      "Access-Control-Allow-Headers": "authorization, content-type, mcp-protocol-version",
                      "Access-Control-Max-Age": "86400"})
    return r


@router.post("/oauth/register")
async def register(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return _json({"error": "invalid_client_metadata", "error_description": "JSON no válido"}, 400)
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not (1 <= len(uris) <= 10) or not all(valid_redirect_uri(u) for u in uris):
        return _json({"error": "invalid_redirect_uri",
                      "error_description": "redirect_uris: entre 1 y 10 direcciones https (o http a localhost)"}, 400)
    method = body.get("token_endpoint_auth_method") or "client_secret_basic"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        return _json({"error": "invalid_client_metadata",
                      "error_description": f"token_endpoint_auth_method no admitido: {method}"}, 400)
    grants = body.get("grant_types") or ["authorization_code"]
    if any(g not in ("authorization_code", "refresh_token") for g in grants):
        return _json({"error": "invalid_client_metadata",
                      "error_description": "Solo se admiten authorization_code y refresh_token"}, 400)
    secret = None if method == "none" else secrets.token_urlsafe(32)
    cid = "lxc_" + secrets.token_urlsafe(16)
    name = (body.get("client_name") or "").strip()[:100] if isinstance(body.get("client_name"), str) else ""
    client = {"client_id": cid, "kind": "dcr", "auth_method": method, "redirect_uris": uris,
              "name": name or _host_of(uris[0]), "uri": (body.get("client_uri") or None),
              "secret_hash": _hash(secret) if secret else None, "created_at": _now(),
              "exp": _now() + TTL["unused_client"]}
    with LOCK:
        d = _load()
        d["clients"][cid] = client
        _save(d)
    out = {"client_id": cid, "client_id_issued_at": client["created_at"], "client_name": client["name"],
           "redirect_uris": uris, "grant_types": ["authorization_code", "refresh_token"],
           "response_types": ["code"], "token_endpoint_auth_method": method, "scope": " ".join(ALL_SCOPES)}
    if secret:
        out.update({"client_secret": secret, "client_secret_expires_at": 0})
    return _json(out, 201, no_store=True)


def _error_page(request: Request, message: str, status: int = 400):
    from app.routers.pages import render, _ctx
    return render(request, "oauth_consent.html", _ctx(request, error=message, req=None, client=None),
                  status_code=status)


@router.get("/oauth/authorize")
async def authorize(request: Request):
    q = request.query_params
    try:
        client = resolve_client(q.get("client_id"))
    except OAuthError as e:
        return _error_page(request, e.description)
    if not client:
        return _error_page(request, "La aplicación no está registrada (client_id desconocido).")
    redirect_uri = match_redirect(client["redirect_uris"], q.get("redirect_uri"))
    if not redirect_uri:
        return _error_page(request, "La dirección de vuelta (redirect_uri) no coincide con la registrada.")
    # Errors are shown here, never redirected: anyone can register a client with any redirect URI.
    if q.get("response_type") != "code":
        return _error_page(request, "Solo se admite response_type=code.")
    challenge = q.get("code_challenge") or ""
    if q.get("code_challenge_method") != "S256" or not (43 <= len(challenge) <= 128):
        return _error_page(request, "PKCE con S256 es obligatorio.")
    if q.get("resource") and not _valid_resource(request, q["resource"]):
        return _error_page(request, f"El recurso debe ser {resource_url(request)}.")
    if len(q.get("state") or "") > 1000:
        return _error_page(request, "state demasiado largo.")
    rid = secrets.token_urlsafe(24)
    with LOCK:
        d = _load()
        d["requests"][rid] = {"client_id": client["client_id"], "redirect_uri": redirect_uri, "state": q.get("state"),
                              "code_challenge": challenge, "scopes": requested_scopes(q.get("scope")),
                              "resource": q.get("resource"), "exp": _now() + TTL["request"]}
        _save(d)
    return RedirectResponse(_base(request) + f"/oauth/consent/{rid}", status_code=302)


def _pending(rid: str) -> tuple[Optional[dict], Optional[dict]]:
    r = _load()["requests"].get(rid)
    if not r:
        return None, None
    try:
        client = resolve_client(r["client_id"])
    except OAuthError:
        client = None
    return r, client


@router.get("/oauth/consent/{rid}", response_class=HTMLResponse)
async def consent_page(request: Request, rid: str):
    from app.routers.pages import render, _ctx
    if not request.state.current_user:
        return RedirectResponse(_base(request) + "/login?next=" + urllib.parse.quote(f"/oauth/consent/{rid}"),
                                status_code=302)
    r, client = _pending(rid)
    if not r or not client:
        return _error_page(request, "La solicitud ha caducado. Vuelve a conectar desde tu asistente.", 404)
    if request.state.user_dir == "public":
        return _error_page(request, "Tu usuario no tiene biblioteca en Lextor, así que no puede conectar asistentes.",
                           403)
    return render(request, "oauth_consent.html", _ctx(
        request, error=None, rid=rid, req=r, client=describe_client(client, r["redirect_uri"]),
        can_write=SCOPE_WRITE in r["scopes"]))


@router.post("/oauth/consent/{rid}")
async def consent_submit(request: Request, rid: str, action: str = Form(...), write: str = Form("")):
    if not request.state.current_user or request.state.user_dir == "public":
        return _error_page(request, "Inicia sesión para autorizar la conexión.", 401)
    with LOCK:
        d = _load()
        r = d["requests"].pop(rid, None)
        _save(d)
    if not r:
        return _error_page(request, "La solicitud ha caducado. Vuelve a conectar desde tu asistente.", 404)
    params = {"iss": _base(request)}
    if r.get("state"):
        params["state"] = r["state"]
    if action != "approve":
        params.update({"error": "access_denied", "error_description": "El usuario no ha autorizado la conexión"})
    else:
        scopes = ALL_SCOPES if (write and SCOPE_WRITE in r["scopes"]) else [SCOPE_READ]
        code = secrets.token_urlsafe(32)
        with LOCK:
            d = _load()
            d["codes"][_hash(code)] = {"client_id": r["client_id"], "redirect_uri": r["redirect_uri"],
                                       "code_challenge": r["code_challenge"], "scopes": scopes,
                                       "resource": r.get("resource"), "user_dir": request.state.user_dir,
                                       "user": request.state.current_user, "exp": _now() + TTL["code"]}
            _save(d)
        params["code"] = code
    sep = "&" if urllib.parse.urlsplit(r["redirect_uri"]).query else "?"
    return RedirectResponse(r["redirect_uri"] + sep + urllib.parse.urlencode(params), status_code=303)


async def _form(request: Request) -> dict:
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        try:
            body = await request.json()
            return {k: str(v) for k, v in body.items()} if isinstance(body, dict) else {}
        except Exception:
            return {}
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


def _authenticate_client(request: Request, body: dict) -> dict:
    cid, secret = body.get("client_id"), body.get("client_secret")
    basic = request.headers.get("authorization", "")
    if basic.lower().startswith("basic "):
        try:
            raw = base64.b64decode(basic[6:]).decode()
            a, _, b = raw.partition(":")
            cid, secret = urllib.parse.unquote(a), urllib.parse.unquote(b)
        except Exception:
            raise OAuthError("invalid_client", "Cabecera Basic no válida", 401)
    try:
        client = resolve_client(cid)
    except OAuthError:
        client = None
    if not client:
        raise OAuthError("invalid_client", "Cliente desconocido", 401)
    if client["auth_method"] != "none":
        if not (client.get("secret_hash") and secret and secrets.compare_digest(_hash(secret), client["secret_hash"])):
            raise OAuthError("invalid_client", "Credenciales del cliente no válidas", 401)
    return client


@router.post("/oauth/token")
async def token(request: Request):
    body = await _form(request)
    try:
        client = _authenticate_client(request, body)
        gt = body.get("grant_type")
        if gt == "authorization_code":
            with LOCK:
                d = _load()
                code = d["codes"].pop(_hash(body.get("code") or ""), None)
                _save(d)
            if not code or code["client_id"] != client["client_id"]:
                raise OAuthError("invalid_grant", "Código no válido o caducado")
            if body.get("redirect_uri") and body["redirect_uri"] != code["redirect_uri"]:
                raise OAuthError("invalid_grant", "redirect_uri no coincide")
            verifier = body.get("code_verifier") or ""
            calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not (43 <= len(verifier) <= 128) or calc != code["code_challenge"]:
                raise OAuthError("invalid_grant", "PKCE: code_verifier no válido")
            if body.get("resource") and not _valid_resource(request, body["resource"]):
                raise OAuthError("invalid_target", "Recurso no válido")
            with LOCK:
                d = _load()
                gid = secrets.token_urlsafe(12)
                grant = {"id": gid, "user_dir": code["user_dir"], "user": code["user"],
                         "client_id": client["client_id"], "client_name": client["name"],
                         "redirect_host": _host_of(code["redirect_uri"]), "scopes": code["scopes"],
                         "created_at": _now(), "last_used_at": _now()}
                d["grants"][gid] = grant
                if client["client_id"] in d["clients"]:
                    d["clients"][client["client_id"]].pop("exp", None)  # used clients don't expire
                tokens = _issue(d, grant)
                _save(d)
            return _json(tokens, no_store=True)
        if gt == "refresh_token":
            with LOCK:
                d = _load()
                h = _hash(body.get("refresh_token") or "")
                rt = d["refresh"].get(h)
                if not rt or rt["client_id"] != client["client_id"]:
                    raise OAuthError("invalid_grant", "Token de renovación no válido")
                grant = d["grants"].get(rt["grant_id"])
                if not grant:
                    raise OAuthError("invalid_grant", "La autorización se ha revocado")
                if rt.get("rotated_at") and _now() - rt["rotated_at"] > TTL["refresh_grace"]:
                    _delete_grant(d, grant["id"])  # replay of a rotated token: possible theft
                    _save(d)
                    log.warning("refresh token replay: grant %s revoked", grant["id"])
                    raise OAuthError("invalid_grant", "Token de renovación ya usado")
                if not rt.get("rotated_at"):
                    rt["rotated_at"] = _now()
                    rt["exp"] = _now() + TTL["refresh_grace"] + 5
                grant["last_used_at"] = _now()
                tokens = _issue(d, grant)
                _save(d)
            return _json(tokens, no_store=True)
        raise OAuthError("unsupported_grant_type", "grant_type no admitido")
    except OAuthError as e:
        return _json({"error": e.error, "error_description": e.description}, e.status, no_store=True)


@router.post("/oauth/revoke")
async def revoke(request: Request):
    body = await _form(request)
    try:
        _authenticate_client(request, body)
    except OAuthError as e:
        return _json({"error": e.error, "error_description": e.description}, e.status)
    tok = body.get("token") or ""
    with LOCK:
        d = _load()
        h = _hash(tok)
        rec = d["access"].pop(h, None) or d["refresh"].get(h)
        if rec and h in d["refresh"]:
            _delete_grant(d, rec["grant_id"])  # revoking a refresh token ends the whole grant
        _save(d)
    return _json({})
