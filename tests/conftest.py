import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def contents(tmp_path, monkeypatch):
    """Isolated content directory with a copy of the public examples."""
    from app.config import settings
    dest = tmp_path / "contents"
    shutil.copytree(ROOT / "static" / "contents" / "public", dest / "public")
    (dest / "admin").mkdir()
    monkeypatch.setattr(settings, "contents_dir", str(dest))
    monkeypatch.setattr(settings, "public_url", "https://lextor.test")
    return dest


@pytest.fixture()
def client(contents):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def example_doc():
    return json.loads((ROOT / "app" / "lextor" / "examples" / "fr-es.json").read_text(encoding="utf-8"))
