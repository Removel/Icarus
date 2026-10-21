"""Managed deployment model must also apply to KBs created through REST."""

import importlib.util
from pathlib import Path

from openkb.cli import initialize_kb
from openkb.config import load_config, save_config


def test_rest_kb_inherits_deployed_model_and_template_options(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "prepare_managed_kb.py"
    spec = importlib.util.spec_from_file_location("prepare_managed_kb", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("openkb.cli.register_kb", lambda path: None)
    template = tmp_path / "config.yaml"
    save_config(template, {"model": "old-model", "language": "zh", "pageindex_threshold": 42})
    module.configure_template("deepseek/deepseek-flash")
    created = tmp_path / "new-kb"
    initialize_kb(created)
    actual = load_config(created / ".openkb" / "config.yaml")
    assert actual["model"] == "deepseek/deepseek-flash"
    assert actual["language"] == "zh"
    assert actual["pageindex_threshold"] == 42
    module.configure_template("deepseek/deepseek-v4-pro")
    assert load_config(created / ".openkb" / "config.yaml") == actual


def test_managed_template_can_be_created_without_existing_config(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "prepare_managed_kb.py"
    spec = importlib.util.spec_from_file_location("prepare_managed_kb", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    module.configure_template("deepseek/deepseek-flash")
    assert load_config(tmp_path / "config.yaml")["model"] == "deepseek/deepseek-flash"
