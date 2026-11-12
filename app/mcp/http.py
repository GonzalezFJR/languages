"""
Streamable-HTTP MCP endpoint for Lextor (hand-written, stateless, JSON responses).

Supports both protocol eras:
  * legacy (≤ 2025-11-25): `initialize` handshake, then requests without per-request _meta;
  * modern (2026-07-28): stateless `server/discover`, per-request `_meta` with protocol version and
    capabilities, `Mcp-Method` / `Mcp-Name` / `MCP-Protocol-Version` headers validated, `resultType`
    on every result and `ttlMs` / `cacheScope` on cacheable results.

Endpoints:
  POST /mcp               anonymous (texts get a public share link) or `Authorization: Bearer <token>`
  POST /mcp/u/{token}     personal connector URL (token in the path; works in every client)
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.lextor.guide import SERVER_INSTRUCTIONS, build_guide
from app.mcp import tools as T
from app.mcp.widget import widget_html
from app.services import token_service

log = logging.getLogger("lextor.mcp")
router = APIRouter(tags=["mcp"])

SERVER_INFO = {"name": "lextor", "title": "Lextor", "version": "2.0.0"}
MODERN_VERSIONS = ["2026-07-28"]
LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
UI_EXT = "io.modelcontextprotocol/ui"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
TTL_MS = 3_600_000

CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {"subscribe": False, "listChanged": False},
    "prompts": {"listChanged": False},
    "extensions": {UI_EXT: {}},
}

RESOURCES = [
    {"uri": T.WIDGET_URI, "name": "lextor_reader", "title": "Lector bilingüe Lextor",
     "description": "Interactive bilingual reader for Lextor texts", "mimeType": "text/html;profile=mcp-app"},
    {"uri": "lextor://guide", "name": "lextor_format_guide", "title": "Guía del formato lextor/1",
     "description": "Exact format and quality rules for Lextor documents", "mimeType": "text/markdown"},
    {"uri": "lextor://example", "name": "lextor_example", "title": "Documento de ejemplo",
     "description": "A complete, valid lextor/1 document", "mimeType": "application/json"},
]


def _widget_ui_meta() -> dict:
    return {
        "ui": {
            "prefersBorder": False,
            "csp": {"connectDomains": [], "resourceDomains": ["https://fonts.googleapis.com",
                                                              "https://fonts.gstatic.com"]},
        },
        "openai/widgetDescription": "Lector bilingüe interactivo: texto original con traducción y notas al tocar.",
        "openai/widgetPrefersBorder": False,
        "openai/widgetCSP": {"connect_domains": [], "resource_domains": ["https://fonts.googleapis.com",
                                                                         "https://fonts.gstatic.com"]},
        "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"]},
    }


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None, http_status: int = 200):
        super().__init__(message)
        self.code, self.message, self.data, self.http_status = code, message, data, http_status


def public_base_url(request: Request) -> str:
    if settings.public_url:
        return settings.public_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    root = request.scope.get("root_path", "") or ""
    return f"{proto}://{host}{root}".rstrip("/")


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    allowed = [o.strip() for o in (settings.mcp_allowed_origins or "*").split(",") if o.strip()]
    if not origin or "*" in allowed:
        return True
    host = request.headers.get("host", "")
    return origin in allowed or origin.endswith("//" + host)


def _decode_header_value(v: Optional[str]) -> Optional[str]:
    if v and v.startswith("=?base64?"):
        raw = v[len("=?base64?"):]
        if raw.endswith("?="):
            raw = raw[:-2]
        try:
            return base64.b64decode(raw).decode("utf-8")
        except Exception:
            return v
    return v


# ──────────────────────────────────────────────────────────── endpoints ──

@router.post("/mcp")
async def mcp_anonymous(request: Request):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    return await _handle(request, token)


@router.post("/mcp/u/{token}")
async def mcp_personal(request: Request, token: str):
    return await _handle(request, token)


@router.api_route("/mcp", methods=["GET", "DELETE"])
@router.api_route("/mcp/u/{token}", methods=["GET", "DELETE"])
async def mcp_not_allowed(request: Request, token: str = ""):
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        # A human opened the connector URL in a browser: explain instead of a bare 405.
        from fastapi.responses import RedirectResponse
        return RedirectResponse((request.scope.get("root_path") or "") + "/connect?from=mcp", status_code=302)
    return Response(status_code=405, headers={"Allow": "POST"})


async def _handle(request: Request, token: Optional[str]):
    if not _origin_allowed(request):
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "Origin not allowed"}}, status_code=403)
    user_dir = None
    if token:
        user_dir = token_service.resolve(token)
        if user_dir is None:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {
                    "code": -32001,
                    "message": "Token de Lextor no válido o revocado. Genera una URL nueva en /connect."}},
                status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                            status_code=400)

    ctx = T.CallContext(user_dir=user_dir, base_url=public_base_url(request))
    if token and user_dir:
        client = request.headers.get("user-agent", "")
        try:
            token_service.touch(token, client)
        except Exception:
            pass

    if isinstance(body, list):  # JSON-RPC batch (2025-03-26)
        out = []
        for msg in body:
            status, resp = _dispatch(request, ctx, msg, batch=True)
            if resp is not None:
                out.append(resp)
        return JSONResponse(out) if out else Response(status_code=202)
    status, resp = _dispatch(request, ctx, body)
    if resp is None:
        return Response(status_code=202)
    return JSONResponse(resp, status_code=status)


def _dispatch(request: Request, ctx: T.CallContext, msg: Any, batch: bool = False) -> tuple[int, Optional[dict]]:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if method is None:  # a response from the client; nothing to do
        return 202, None
    is_notification = "id" not in msg
    meta = params.get("_meta") or {}
    modern = META_VERSION in meta
    try:
        if modern:
            _validate_modern(request, method, params, meta)
        result = _route(ctx, method, params, modern)
        if is_notification:
            return 202, None
        result.setdefault("resultType", "complete")
        result.setdefault("_meta", {})
        if modern:
            result["_meta"]["io.modelcontextprotocol/serverInfo"] = SERVER_INFO
        if not result["_meta"]:
            result.pop("_meta")
        log.info("mcp %s %s era=%s user=%s", method, params.get("name", ""), "modern" if modern else "legacy",
                 ctx.user_dir or "anon")
        return 200, {"jsonrpc": "2.0", "id": mid, "result": result}
    except RpcError as e:
        if is_notification:
            return 202, None
        err = {"code": e.code, "message": e.message}
        if e.data is not None:
            err["data"] = e.data
        status = e.http_status if (modern or e.http_status >= 400) and not batch else 200
        return status, {"jsonrpc": "2.0", "id": mid, "error": err}
    except Exception as e:  # pragma: no cover - defensive
        log.exception("MCP internal error")
        if is_notification:
            return 202, None
        return 200, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"Internal error: {e}"}}


def _validate_modern(request: Request, method: str, params: dict, meta: dict) -> None:
    version = meta.get(META_VERSION)
    if version not in MODERN_VERSIONS:
        raise RpcError(-32022, "Unsupported protocol version",
                       {"supported": MODERN_VERSIONS + LEGACY_VERSIONS, "requested": version}, 400)
    hv = request.headers.get("mcp-protocol-version")
    if hv != version:
        raise RpcError(-32020, f"Header mismatch: MCP-Protocol-Version header {hv!r} does not match body "
                               f"value {version!r}", None, 400)
    hm = request.headers.get("mcp-method")
    if hm != method:
        raise RpcError(-32020, f"Header mismatch: Mcp-Method header {hm!r} does not match body value {method!r}",
                       None, 400)
    if method in ("tools/call", "resources/read", "prompts/get"):
        expected = params.get("uri") if method == "resources/read" else params.get("name")
        hn = _decode_header_value(request.headers.get("mcp-name"))
        if hn != expected:
            raise RpcError(-32020, f"Header mismatch: Mcp-Name header {hn!r} does not match body value "
                                   f"{expected!r}", None, 400)


def _cacheable(result: dict, scope: str = "public") -> dict:
    result["ttlMs"] = TTL_MS
    result["cacheScope"] = scope
    return result


def _route(ctx: T.CallContext, method: str, params: dict, modern: bool) -> dict:
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return {"protocolVersion": version, "capabilities": CAPABILITIES, "serverInfo": SERVER_INFO,
                "instructions": SERVER_INSTRUCTIONS}
    if method == "server/discover":
        return _cacheable({"supportedVersions": MODERN_VERSIONS + LEGACY_VERSIONS, "capabilities": CAPABILITIES,
                           "instructions": SERVER_INSTRUCTIONS})
    if method.startswith("notifications/"):
        return {}
    if method == "ping":
        return {}
    if method == "logging/setLevel":
        return {}
    if method == "tools/list":
        return _cacheable({"tools": T.TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if name not in T.TOOLS_BY_NAME:
            raise RpcError(-32602, f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise RpcError(-32602, "arguments must be an object")
        return T.call_tool(ctx, name, args)
    if method == "resources/list":
        res = []
        for r in RESOURCES:
            r = dict(r)
            if r["uri"] == T.WIDGET_URI:
                r["_meta"] = _widget_ui_meta()
            res.append(r)
        return _cacheable({"resources": res})
    if method == "resources/templates/list":
        return _cacheable({"resourceTemplates": []})
    if method == "resources/read":
        uri = params.get("uri")
        if uri == T.WIDGET_URI:
            content = {"uri": uri, "mimeType": "text/html;profile=mcp-app", "text": widget_html(),
                       "_meta": _widget_ui_meta()}
        elif uri == "lextor://guide":
            content = {"uri": uri, "mimeType": "text/markdown", "text": build_guide()}
        elif uri == "lextor://example":
            content = {"uri": uri, "mimeType": "application/json", "text": T.example_resource()}
        else:
            raise RpcError(-32002, f"Resource not found: {uri}", {"uri": uri}, 404)
        return _cacheable({"contents": [content]})
    if method == "prompts/list":
        return _cacheable({"prompts": T.PROMPTS})
    if method == "prompts/get":
        try:
            return T.get_prompt(params.get("name"), params.get("arguments") or {})
        except KeyError:
            raise RpcError(-32602, f"Unknown prompt: {params.get('name')}")
    if method == "completion/complete":
        return {"completion": {"values": [], "hasMore": False}}
    raise RpcError(-32601, f"Method not found: {method}", None, 404)
