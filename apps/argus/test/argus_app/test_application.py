"""Argus process/lifecycle and API contract tests; no live external services."""
import importlib
from pathlib import Path

import pytest
from pydantic import SecretStr

from argus_app.config import ArgusConfig


def test_application_exposes_composed_lifespan_not_copied_runtime(tmp_path):
    try:
        module = importlib.import_module("argus_app.application")
    except ModuleNotFoundError:
        module = None
    assert module is not None, "Argus needs application-owned composition and public create_app"
    config = ArgusConfig(database_url=SecretStr("postgresql+asyncpg://unused/argus_test"),
                         data_dir=tmp_path, agent_token=SecretStr("fixture-agent"),
                         gateway_token=SecretStr("fixture-gateway"), auto_scan=False)
    app = module.create_app(config)
    paths = {route.path for route in app.routes}
    assert {"/health", "/agent/events", "/channels", "/protocols/qq", "/protocols/feishu"} <= paths


def test_start_script_runs_installed_argus_module_without_root_pythonpath():
    root = Path(__file__).resolve().parents[4]
    path = root / "apps/argus/scripts/start.sh"
    assert path.is_file(), "Argus should start through the existing lifecycle"
    script = path.read_text()
    assert "argus_app.main" in script
    assert "PYTHONPATH" not in script
