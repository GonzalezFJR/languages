import json

MODERN = "2026-07-28"


def rpc(client, method, params=None, url="/mcp/public", id=1, headers=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id is not None:
        body["id"] = id
    return client.post(url, json=body, headers={"Accept": "application/json, text/event-stream", **(headers or {})})


def modern(client, method, params=None, url="/mcp/public", name=None, **kw):
    params = dict(params or {})
    params["_meta"] = {"io.modelcontextprotocol/protocolVersion": MODERN,
                       "io.modelcontextprotocol/clientCapabilities": {}}
    h = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
    if name:
        h["Mcp-Name"] = name
    h.update(kw.pop("headers", {}))
    return rpc(client, method, params, url, headers=h, **kw)


def personal_url(client):
    from app.services import token_service
    tok = token_service.get_or_create("admin")["token"]
    return f"/mcp/u/{tok}"


# ── protocol ─────────────────────────────────────────────────────────────

def test_legacy_initialize_and_lists(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}})
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert "io.modelcontextprotocol/ui" in res["capabilities"]["extensions"]
    assert "lextor_get_format_guide" in res["instructions"]
    assert rpc(client, "notifications/initialized", id=None).status_code == 202
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"lextor_create_text", "lextor_validate", "lextor_append_blocks", "lextor_show_text"} <= names
    create = next(t for t in tools if t["name"] == "lextor_create_text")
    assert create["_meta"]["ui"]["resourceUri"].startswith("ui://lextor/")
    assert create["_meta"]["openai/outputTemplate"] == create["_meta"]["ui"]["resourceUri"]


def test_unknown_legacy_version_negotiates_latest(client):
    res = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert res["protocolVersion"] == "2025-11-25"


def test_modern_discover_and_headers(client):
    r = modern(client, "server/discover")
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["resultType"] == "complete" and res["cacheScope"] == "public" and res["ttlMs"] > 0
    assert "2026-07-28" in res["supportedVersions"]
    assert res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "lextor"
    # header mismatch
    bad = modern(client, "tools/list", headers={"Mcp-Method": "tools/call"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32020
    # unsupported version
    body = {"jsonrpc": "2.0", "id": 3, "method": "tools/list",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2099-01-01"}}}
    r = client.post("/mcp/public", json=body, headers={"MCP-Protocol-Version": "2099-01-01", "Mcp-Method": "tools/list"})
    assert r.status_code == 400 and r.json()["error"]["code"] == -32022
    assert "supported" in r.json()["error"]["data"]
    # unknown method
    r = modern(client, "foo/bar")
    assert r.status_code == 404 and r.json()["error"]["code"] == -32601


def test_modern_tools_call_needs_mcp_name(client):
    r = modern(client, "tools/call", {"name": "lextor_get_format_guide", "arguments": {}})
    assert r.status_code == 400
    r = modern(client, "tools/call", {"name": "lextor_get_format_guide", "arguments": {"text_lang": "ja"}},
               name="lextor_get_format_guide")
    res = r.json()["result"]
    assert res["resultType"] == "complete"
    text = res["content"][0]["text"]
    assert "reading" in text and "rōmaji" in text


def test_widget_resource(client):
    lst = rpc(client, "resources/list").json()["result"]["resources"]
    w = next(r for r in lst if r["uri"].startswith("ui://"))
    assert w["mimeType"] == "text/html;profile=mcp-app"
    read = rpc(client, "resources/read", {"uri": w["uri"]}).json()["result"]["contents"][0]
    assert read["mimeType"] == "text/html;profile=mcp-app"
    assert "ui/initialize" in read["text"] and "LextorReader" in read["text"]
    assert "https://fonts.googleapis.com" in read["_meta"]["ui"]["csp"]["resourceDomains"]


def test_get_without_html_is_405_and_browser_redirects(client):
    assert client.get("/mcp").status_code == 405
    r = client.get("/mcp", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("/connect?from=mcp")


def test_invalid_token_is_401(client):
    r = rpc(client, "tools/list", url="/mcp/u/nope")
    assert r.status_code == 401


# ── tools ────────────────────────────────────────────────────────────────

def call(client, name, args, url="/mcp/public"):
    r = rpc(client, "tools/call", {"name": name, "arguments": args}, url=url)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_validate_tool_reports_tests(client, example_doc):
    res = call(client, "lextor_validate", {"document": example_doc})
    assert res["structuredContent"]["ok"] is True
    assert len(res["structuredContent"]["checks"]) >= 10
    broken = json.loads(json.dumps(example_doc))
    broken["blocks"][1]["sentences"][0]["chunks"][0]["text"] = "Chaque soir,"
    res = call(client, "lextor_validate", {"document": broken})
    assert res["structuredContent"]["ok"] is False
    assert "E_CHUNK_NOT_FOUND" in res["content"][0]["text"]


def test_anonymous_create_returns_share_link_and_widget_data(client, example_doc):
    res = call(client, "lextor_create_text", {"document": example_doc})
    assert not res["isError"]
    sc = res["structuredContent"]
    assert sc["url"].startswith("https://lextor.test/t/")
    assert sc["text_id"].startswith("share:")
    assert res["_meta"]["lextor/document"]["meta"]["title"] == "Le café du coin"
    page = client.get(sc["url"].replace("https://lextor.test", ""))
    assert page.status_code == 200 and "Le café du coin" in page.text
    # anonymous connections cannot list a library
    assert call(client, "lextor_list_projects", {})["isError"]


def test_create_with_errors_saves_nothing(client, example_doc, contents):
    url = personal_url(client)
    bad = json.loads(json.dumps(example_doc))
    bad["blocks"][1]["sentences"][0]["chunks"] = [{"text": "nope", "gloss": "x"}]
    res = call(client, "lextor_create_text", {"document": bad}, url=url)
    assert res["isError"] and "E_CHUNK_NOT_FOUND" in res["content"][0]["text"]
    assert not list((contents / "admin").glob("*/translates/*.lextor"))


def test_personal_flow_create_append_show_get(client, example_doc, contents):
    url = personal_url(client)
    first = json.loads(json.dumps(example_doc))
    rest = first["blocks"][2:]
    first["blocks"] = first["blocks"][:2]
    res = call(client, "lextor_create_text", {"document": first}, url=url)
    assert not res["isError"], res["content"][0]["text"]
    text_id = res["structuredContent"]["text_id"]
    project_id = text_id.split("/")[0]
    assert (contents / "admin" / project_id / "metadata.json").exists()  # auto-created project fr→es
    projects = call(client, "lextor_list_projects", {}, url=url)["structuredContent"]["projects"]
    assert projects[0]["text_lang"] == "fr" and projects[0]["notes_lang"] == "es"

    res = call(client, "lextor_append_blocks", {"text_id": text_id, "blocks": rest}, url=url)
    assert not res["isError"]
    assert res["structuredContent"]["stats"]["blocks"] == 4

    shown = call(client, "lextor_show_text", {"text_id": text_id}, url=url)
    assert len(shown["_meta"]["lextor/document"]["blocks"]) == 4
    got = call(client, "lextor_get_text", {"text_id": text_id, "as": "plain"}, url=url)
    assert "Il pleut sur la ville" in got["content"][0]["text"]

    # second text with the same title gets a distinct file name
    res2 = call(client, "lextor_create_text", {"document": example_doc}, url=url)
    assert res2["structuredContent"]["text_id"] != text_id
    listing = call(client, "lextor_list_texts", {"project_id": project_id}, url=url)
    assert len(listing["structuredContent"]["texts"]) == 2


def test_update_keeps_valid_annotations(client, example_doc):
    url = personal_url(client)
    res = call(client, "lextor_create_text", {"document": example_doc}, url=url)
    text_id = res["structuredContent"]["text_id"]
    pid, fname = text_id.split("/")
    from app.services import text_service as ts
    ts.set_annotation(pid, fname, "b1.s0.c0", {"color": "yellow"}, "admin")
    doc2 = json.loads(json.dumps(example_doc))
    doc2["meta"]["title"] = "Nuevo título"
    res = call(client, "lextor_update_text", {"text_id": text_id, "document": doc2}, url=url)
    assert not res["isError"]
    saved = ts.load_text(pid, fname, "admin")
    assert saved["meta"]["title"] == "Nuevo título"
    assert saved["annotations"] == {"b1.s0.c0": {"color": "yellow"}}


def test_prompts(client):
    prompts = rpc(client, "prompts/list").json()["result"]["prompts"]
    assert {p["name"] for p in prompts} == {"bilingual_text", "graded_reader"}
    got = rpc(client, "prompts/get", {"name": "bilingual_text", "arguments": {"text": "Hola", "level": "B1"}})
    assert "lextor_create_text" in got.json()["result"]["messages"][0]["content"]["text"]


def test_batch_requests(client):
    r = client.post("/mcp/public", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                  {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert r.status_code == 200 and len(r.json()) == 1


def test_anonymous_writes_can_be_disabled(client, example_doc, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_allow_anonymous", False)
    assert rpc(client, "tools/list").status_code == 404
    assert not call(client, "lextor_create_text", {"document": example_doc}, url=personal_url(client))["isError"]
