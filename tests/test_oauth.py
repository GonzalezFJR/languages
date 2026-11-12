import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login(client):
    assert client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=False).status_code == 302


def mcp(client, method, params=None, token=None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=h)


def authorize(client, client_id, write=True, scope="lextor:read lextor:write"):
    verifier, challenge = pkce()
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "st8",
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": scope,
        "resource": "https://lextor.test/mcp"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    consent = r.headers["location"]
    assert consent.startswith("https://lextor.test/oauth/consent/")
    path = urlsplit(consent).path
    page = client.get(path)
    assert page.status_code == 200 and "quiere conectarse" in page.text
    data = {"action": "approve", **({"write": "1"} if write else {})}
    r = client.post(path, data=data, follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert r.headers["location"].startswith(REDIRECT) and q["state"] == ["st8"] and q["iss"] == ["https://lextor.test"]
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id,
                                            "redirect_uri": REDIRECT, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    return tok.json()


def register(client, **extra):
    r = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT],
                                             "token_endpoint_auth_method": "none",
                                             "grant_types": ["authorization_code", "refresh_token"], **extra})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def test_unauthenticated_mcp_gets_oauth_challenge(client):
    r = mcp(client, "initialize", {"protocolVersion": "2025-06-18"})
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert 'resource_metadata="https://lextor.test/.well-known/oauth-protected-resource/mcp"' in www
    assert mcp(client, "ping", token="lxa_bogus").headers["www-authenticate"].count('error="invalid_token"') == 1
    pr = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"] == "https://lextor.test/mcp" and pr["authorization_servers"] == ["https://lextor.test"]
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"] and asm["client_id_metadata_document_supported"]
    assert client.get("/.well-known/openid-configuration").json()["token_endpoint"].endswith("/oauth/token")


def test_full_flow_dcr(client, example_doc, contents):
    cid = register(client)
    # consent requires login
    v, ch = pkce()
    r = client.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
                                               "code_challenge": ch, "code_challenge_method": "S256"},
                   follow_redirects=False)
    r2 = client.get(urlsplit(r.headers["location"]).path, follow_redirects=False)
    assert r2.status_code == 302 and "/login?next=" in r2.headers["location"]
    login(client)
    tokens = authorize(client, cid)
    assert tokens["token_type"] == "Bearer" and tokens["scope"] == "lextor:read lextor:write"
    at = tokens["access_token"]
    assert mcp(client, "initialize", {"protocolVersion": "2025-06-18"}, token=at).status_code == 200
    res = mcp(client, "tools/call", {"name": "lextor_create_text", "arguments": {"document": example_doc}}, token=at).json()["result"]
    assert not res["isError"], res["content"][0]["text"]
    assert list((contents / "admin").glob("*/translates/*.lextor"))
    conns = client.get("/api/mcp/connections").json()["connections"]
    assert len(conns) == 1 and conns[0]["name"] == "Claude" and conns[0]["can_write"]

    # refresh rotation; replaying the old refresh token after the grace period revokes everything
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": cid})
    assert r.status_code == 200
    new = r.json()
    assert mcp(client, "ping", token=new["access_token"]).status_code == 200
    from app.mcp import oauth
    d = oauth._load()
    for v in d["refresh"].values():
        if v.get("rotated_at"):
            v["rotated_at"] -= 3600
            v["exp"] += 7200
    oauth._save(d)
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": cid})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert mcp(client, "ping", token=new["access_token"]).status_code == 401
    assert client.get("/api/mcp/connections").json()["connections"] == []


def test_read_only_grant_cannot_write(client, example_doc):
    login(client)
    cid = register(client)
    at = authorize(client, cid, write=False)["access_token"]
    res = mcp(client, "tools/call", {"name": "lextor_create_text", "arguments": {"document": example_doc}}, token=at).json()["result"]
    assert res["isError"] and "lectura" in res["content"][0]["text"]
    assert not mcp(client, "tools/call", {"name": "lextor_list_projects", "arguments": {}}, token=at).json()["result"]["isError"]


def test_pkce_and_redirect_are_enforced(client):
    login(client)
    cid = register(client)
    bad = client.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": "https://evil.example/cb",
                                                 "code_challenge": "x" * 43, "code_challenge_method": "S256"}, follow_redirects=False)
    assert bad.status_code == 400 and "redirect_uri" in bad.text
    nopkce = client.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT},
                        follow_redirects=False)
    assert nopkce.status_code == 400
    verifier, challenge = pkce()
    r = client.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
                                               "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False)
    r = client.post(urlsplit(r.headers["location"]).path, data={"action": "approve", "write": "1"}, follow_redirects=False)
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    wrong = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                              "code_verifier": secrets.token_urlsafe(48)})
    assert wrong.status_code == 400 and "PKCE" in wrong.json()["error_description"]
    # the code is single-use even after a failed attempt
    assert client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                             "code_verifier": verifier}).status_code == 400


def test_deny_redirects_with_error(client):
    login(client)
    cid = register(client)
    _, challenge = pkce()
    r = client.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
                                               "code_challenge": challenge, "code_challenge_method": "S256", "state": "s"},
                   follow_redirects=False)
    r = client.post(urlsplit(r.headers["location"]).path, data={"action": "deny"}, follow_redirects=False)
    assert parse_qs(urlsplit(r.headers["location"]).query)["error"] == ["access_denied"]


def test_cimd_client(client, monkeypatch):
    from app.mcp import oauth
    cid = "https://claude.ai/oauth/mcp-oauth-client-metadata"
    monkeypatch.setattr(oauth, "cimd_fetcher", lambda url: {
        "client_id": cid, "client_name": "Claude", "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "none"})
    login(client)
    tokens = authorize(client, cid)
    assert mcp(client, "ping", token=tokens["access_token"]).status_code == 200
    assert client.post("/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": cid}).status_code == 200
    assert mcp(client, "ping", token=tokens["access_token"]).status_code == 401


def test_personal_token_still_works_in_header(client):
    from app.services import token_service
    tok = token_service.get_or_create("admin")["token"]
    assert mcp(client, "ping", token=tok).status_code == 200


def test_redirects_are_absolute(client):
    r = client.get("/home", follow_redirects=False)
    assert r.headers["location"] == "https://lextor.test/library"
