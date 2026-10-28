"""The Vercel entrypoint (backend/index.py) must expose the same read-only FastAPI app."""

from __future__ import annotations

import json
import runpy
import tomllib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[2]


def test_index_exposes_fastapi_app_named_app():
    namespace = runpy.run_path(str(BACKEND / "index.py"))
    app = namespace["app"]
    assert isinstance(app, FastAPI)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.post("/api/v1/runs").status_code == 405


def test_vercel_config_matches_entrypoint_and_region():
    config = json.loads((BACKEND / "vercel.json").read_text(encoding="utf-8"))
    assert config["framework"] == "fastapi"
    assert config["regions"] == ["sin1"]  # same region as the Neon database (Singapore)
    function = config["functions"]["index.py"]
    assert "tests/**" in function["excludeFiles"] and "config/**" in function["excludeFiles"]


def test_api_runtime_dependencies_are_core_dependencies():
    """Vercel installs only [project.dependencies]; the API must not rely on optional extras."""
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = " ".join(project["dependencies"])
    for needed in ("fastapi", "sqlalchemy", "psycopg"):
        assert needed in core
    for heavy in ("pandas", "pandera", "alembic", "uvicorn"):
        assert heavy not in core
