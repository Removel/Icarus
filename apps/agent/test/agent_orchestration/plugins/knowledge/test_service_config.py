import importlib.util
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[6]
SCRIPT = REPO_ROOT / "apps" / "openkb" / "scripts" / "icarus_compose.py"
COMPOSE = REPO_ROOT / "apps" / "openkb" / "docker-compose.yaml"
PYPROJECT = REPO_ROOT / "apps" / "openkb" / "pyproject.toml"


def load_module():
    spec = importlib.util.spec_from_file_location("icarus_openkb_compose", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_openkb_compose固定数据目录和config覆盖():
    service = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["openkb"]
    assert "env_file" not in service
    assert service["environment"]["OPENKB_CONFIG_DIR"] == "/data/config"
    assert service["environment"]["OPENKB_KB_ROOT"] == "/data/kbs"
    assert any("services/openkb/config:/data/config" in item for item in service["volumes"])
    assert any("services/openkb/kbs:/data/kbs" in item for item in service["volumes"])
    assert any("services/openkb/backups:/data/backups" in item for item in service["volumes"])


def test_openkb源码在无git元数据时使用固定构建版本():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'version = "0.1.0+icarus"' in text
    assert 'source = "vcs"' not in text


def test_openkb_compose脚本安全解析根env并创建目录(tmp_path, monkeypatch):
    module = load_module()
    fake_repo = tmp_path / "repo"
    script = fake_repo / "apps" / "openkb" / "scripts" / "icarus_compose.py"
    script.parent.mkdir(parents=True)
    data_dir = tmp_path / "data with space"
    (fake_repo / "apps" / "agent").mkdir(parents=True)
    (fake_repo / "apps" / "agent" / "settings.json").write_text(
        '{"runtime":{"plugin_config":{"knowledge":{"knowledge_base":"test-kb"}}}}',
        encoding="utf-8",
    )
    (fake_repo / ".env").write_text(
        f"ICARUS_DATA_DIR='{data_dir}'\n"
        "ICARUS_OPENKB_API_TOKEN='token-with-$-chars-long'\n"
        "OPENAI_API_KEY='model$key'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "__file__", str(script))
    monkeypatch.setattr(
        module.shutil, "which",
        lambda name: "/fake/docker-compose" if name == "docker-compose" else None,
    )
    recorded = {}

    def run(command, *, env, check):
        recorded.update({"command": command, "env": env, "check": check})
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.sys, "argv", [str(script), "config"])
    assert module.main() == 0
    assert recorded["env"]["ICARUS_OPENKB_API_TOKEN"] == "token-with-$-chars-long"
    assert recorded["env"]["ICARUS_OPENKB_LLM_API_KEY"] == "model$key"
    assert recorded["env"]["ICARUS_OPENKB_KNOWLEDGE_BASE"] == "test-kb"
    assert (data_dir / "services" / "openkb" / "config").is_dir()
    assert (data_dir / "services" / "openkb" / "kbs").is_dir()
    assert recorded["command"][-1] == "config"


def test_openkb_compose脚本缺少docker时给出明确错误(tmp_path, monkeypatch):
    module = load_module()
    fake_repo = tmp_path / "repo"
    script = fake_repo / "apps" / "openkb" / "scripts" / "icarus_compose.py"
    script.parent.mkdir(parents=True)
    (fake_repo / "apps" / "agent").mkdir(parents=True)
    (fake_repo / "apps" / "agent" / "settings.json").write_text(
        '{"runtime":{"plugin_config":{}}}', encoding="utf-8"
    )
    (fake_repo / ".env").write_text(
        f"ICARUS_DATA_DIR={tmp_path / 'data'}\n"
        "ICARUS_OPENKB_API_TOKEN=token-long-enough-for-test\n"
        "OPENAI_API_KEY=model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "__file__", str(script))
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="requires Docker"):
        module.main()


def test_openkb_compose停止不依赖agent环境或完整运行配置(tmp_path, monkeypatch):
    module = load_module()
    fake_repo = tmp_path / "repo"
    script = fake_repo / "apps" / "openkb" / "scripts" / "icarus_compose.py"
    script.parent.mkdir(parents=True)
    monkeypatch.setattr(module, "__file__", str(script))
    monkeypatch.setattr(
        module.shutil, "which",
        lambda name: "/fake/docker-compose" if name == "docker-compose" else None,
    )
    recorded = {}

    def run(command, *, env, check):
        recorded.update({"command": command, "env": env, "check": check})
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.sys, "argv", [str(script), "down"])

    assert module.main() == 0
    assert recorded["command"][-1] == "down"
    assert recorded["env"]["ICARUS_OPENKB_LLM_API_KEY"] == "not-used"
