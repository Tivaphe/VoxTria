"""Fixtures partagées : isole chaque test dans un répertoire temporaire."""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Copie le projet dans un tmpdir et y recharge les modules.

    Évite qu'un test écrive dans le vrai config.json ou le vrai ./voices.
    """
    for f in ("config_schema.py", "pipeline.py", "server.py"):
        shutil.copy(REPO / f, tmp_path / f)
    # config.json est ignoré par git : on part de l'exemple s'il est absent.
    src_cfg = REPO / "config.json"
    if not src_cfg.exists():
        src_cfg = REPO / "config.example.json"
    shutil.copy(src_cfg, tmp_path / "config.json")
    (tmp_path / "voices").mkdir()
    (tmp_path / "_out").mkdir()

    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in ("config_schema", "pipeline", "server"):
        sys.modules.pop(mod, None)
    yield tmp_path
    for mod in ("config_schema", "pipeline", "server"):
        sys.modules.pop(mod, None)


@pytest.fixture
def server_mod(tmp_home):
    import config_schema  # noqa: F401
    return importlib.import_module("server")


@pytest.fixture
def client(server_mod):
    return TestClient(server_mod.app, raise_server_exceptions=False)


@pytest.fixture
def cfg_file(tmp_home):
    def _read():
        return json.loads((tmp_home / "config.json").read_text(encoding="utf-8"))
    return _read
