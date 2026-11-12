import json

import pytest


def login(client):
    r = client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=False)
    assert r.status_code == 302


def test_static_contents_are_not_served(client, contents, monkeypatch):
    assert client.get("/static/contents/public/frances_elemental/metadata.json").status_code == 404
    assert client.get("/static/contents/_system/tokens.json").status_code == 404
    assert client.get("/static/css/app.css").status_code == 200


def test_static_contents_blocked_against_traversal(monkeypatch):
    """Production keeps data inside ./static/contents (verified manually with uvicorn --root-path too)."""
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    monkeypatch.setattr(settings, "contents_dir", "static/contents")
    with TestClient(app) as c:
        assert c.get("/static/css/app.css").status_code == 200
        for url in ("/static/contents/public/frances_elemental/metadata.json",
                    "/static/css/../contents/public/frances_elemental/metadata.json",
                    "/static/css/%2e%2e/contents/public/frances_elemental/metadata.json"):
            assert c.get(url).status_code == 404, url


@pytest.fixture()
def legacy_project(contents):
    """A legacy .xlan project (multi-part text, user notes, folders) in the admin space."""
    pdir = contents / "admin" / "viejo"
    (pdir / "translates").mkdir(parents=True)
    (pdir / "docs").mkdir()
    (pdir / "metadata.json").write_text(json.dumps({"id": "viejo", "name": "Viejo", "base": "es", "target": "fr"}))
    (pdir / "docs" / "metadata.json").write_text(json.dumps({"categories": [], "files": {}}))
    part = lambda txt, note=None: {"meta": {"title": "Cuento", "text_language": "fr", "notes_language": "es"},
                                   "content": [{"type": "paragraph", "segments": [
                                       {"id": "s1", "text": txt, "translation": "x", "info": "i",
                                        **({"user_note": note} if note else {})}]}]}
    (pdir / "translates" / "cuento.xlan").write_text(json.dumps(part("Il était une fois.", {"highlight_color": "pink", "comment": "!"})))
    (pdir / "translates" / "cuento_2.xlan").write_text(json.dumps(part("Fin.")))
    (pdir / "translates" / "metadata.json").write_text(json.dumps({
        "categories": [{"id": "c1", "name": "Cuentos", "subcategories": [], "items": ["cuento.xlan"]}],
        "files": {"cuento.xlan": {"name": "cuento.xlan", "display_name": "Cuento", "parts": ["cuento.xlan", "cuento_2.xlan"]}}}))
    return pdir


def test_legacy_projects_are_migrated_on_startup(legacy_project, client):
    tdir = legacy_project / "translates"
    assert not list(tdir.glob("*.xlan"))
    assert (tdir / "_legacy" / "cuento.xlan").exists() and (tdir / "_legacy" / "cuento_2.xlan").exists()
    doc = json.loads((tdir / "cuento.lextor").read_text())
    assert [s["text"] for b in doc["blocks"] for s in b["sentences"]] == ["Il était une fois.", "Fin."]
    assert doc["annotations"] == {"b0.s0.c0": {"color": "pink", "comment": "!"}}
    meta = json.loads((tdir / "metadata.json").read_text())
    assert list(meta["files"]) == ["cuento.lextor"] and "parts" not in meta["files"]["cuento.lextor"]
    assert meta["categories"][0]["items"] == ["cuento.lextor"]
    assert meta["files"]["cuento.lextor"]["share_id"]


def test_hidden_legacy_filename_gets_a_real_name(contents, client):
    from app.services import text_service as ts
    tdir = contents / "admin" / "p" / "translates"
    tdir.mkdir(parents=True)
    (contents / "admin" / "p" / "metadata.json").write_text(json.dumps({"id": "p", "name": "P", "base": "es", "target": "ru"}))
    (tdir / ".xlan").write_text(json.dumps({"meta": {"title": "Pegado", "text_language": "ru", "notes_language": "es"},
        "content": [{"type": "paragraph", "segments": [{"text": "Привет", "translation": "Hola", "info": "x"}]}]}))
    (tdir / "metadata.json").write_text(json.dumps({"categories": [], "files": {".xlan": {"name": ".xlan"}}}))
    ts.migrate_legacy_all()
    assert (tdir / "pegado.lextor").exists() and not (tdir / ".xlan.lextor").exists()
    assert list(json.loads((tdir / "metadata.json").read_text())["files"]) == ["pegado.lextor"]


def test_bundled_examples_are_lextor(contents):
    assert not list(contents.glob("public/*/translates/*.xlan"))
    assert len(list(contents.glob("public/*/translates/*.lextor"))) >= 14


def test_pages_render(client):
    for path in ("/", "/library", "/connect", "/format", "/login", "/project/frances_elemental",
                 "/read/frances_elemental/au_marche.lextor"):
        r = client.get(path)
        assert r.status_code == 200, path
    r = client.get("/viewer/frances_elemental/xlan/au_marche.xlan", follow_redirects=False)
    assert r.headers["location"].endswith("/read/frances_elemental/au_marche.lextor")


def test_public_space_is_read_only(client):
    assert client.post("/api/projects", json={"name": "x", "base": "es", "target": "fr"}).status_code == 401
    assert client.delete("/api/projects/frances_elemental").status_code == 401


def test_import_and_annotate(client, example_doc):
    login(client)
    p = client.post("/api/projects", json={"name": "Francés", "base": "es", "target": "fr"}).json()
    files = {"file": ("cafe.lextor", json.dumps(example_doc), "application/json")}
    r = client.post(f"/api/projects/{p['id']}/texts/import", files=files)
    assert r.status_code == 200, r.text
    fname = r.json()["filename"]
    r = client.put(f"/api/projects/{p['id']}/texts/{fname}/annotations/b1.s0.c2", json={"color": "green", "comment": "ojo"})
    assert r.status_code == 200
    doc = client.get(f"/api/projects/{p['id']}/texts/{fname}").json()
    assert doc["annotations"]["b1.s0.c2"] == {"color": "green", "comment": "ojo"}
    bad = {"file": ("bad.json", json.dumps({"meta": {}, "blocks": []}), "application/json")}
    r = client.post(f"/api/projects/{p['id']}/texts/import", files=bad)
    assert r.status_code == 422 and r.json()["report"]["ok"] is False


def test_connect_page_shows_personal_url_when_logged_in(client):
    login(client)
    r = client.get("/connect")
    assert "/mcp/u/lx_" in r.text
