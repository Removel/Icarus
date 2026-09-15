import importlib.util
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[6]
SCRIPT = REPO_ROOT / "apps" / "mem0" / "scripts" / "icarus_compose.py"
COMPOSE = REPO_ROOT / "apps" / "mem0" / "server" / "docker-compose.yaml"


def load_module():
    spec = importlib.util.spec_from_file_location("icarus_mem0_compose", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_mem0_compose使用可见数据目录且运行导入源码():
    config = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    mem0 = config["services"]["mem0"]
    postgres = config["services"]["postgres"]
    assert "volumes" not in config
    assert any(
        "services/mem0/history:/app/history" in value
        for value in mem0["volumes"]
    )
    assert any(
        "services/mem0/models:/root/.cache/fastembed" in value
        for value in mem0["volumes"]
    )
    assert any(
        "services/mem0/postgres:/var/lib/postgresql/data" in value
        for value in postgres["volumes"]
    )
    assert "force-reinstall" not in mem0["command"]
    assert ".:/app" not in mem0["volumes"]


def test_mem0_compose脚本安全解析env并创建目录(tmp_path, monkeypatch):
    module = load_module()
    fake_repo = tmp_path / "repo"
    script = fake_repo / "apps" / "mem0" / "scripts" / "icarus_compose.py"
    script.parent.mkdir(parents=True)
    env_path = fake_repo / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "data with space"
    env_path.write_text(
        "ICARUS_DATA_DIR='" + str(data_dir) + "'\n"
        "ICARUS_MEM0_POSTGRES_PASSWORD='p$a ss'\n"
        "ICARUS_MEM0_JWT_SECRET='j$w t'\n"
        "ICARUS_MEM0_API_KEY='key-with-$-chars'\n"
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
    assert recorded["env"]["ICARUS_MEM0_API_KEY"] == "key-with-$-chars"
    assert recorded["env"]["ICARUS_MEM0_LLM_API_KEY"] == "model$key"
    assert (data_dir / "services" / "mem0" / "postgres").is_dir()
    assert (data_dir / "services" / "mem0" / "models").is_dir()
    assert recorded["command"][-1] == "config"
    assert recorded["command"][0] == "/fake/docker-compose"


def test_mem0_compose脚本缺少docker时给出明确错误(tmp_path, monkeypatch):
    module = load_module()
    fake_repo = tmp_path / "repo"
    script = fake_repo / "apps" / "mem0" / "scripts" / "icarus_compose.py"
    script.parent.mkdir(parents=True)
    env_path = fake_repo / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        f"ICARUS_DATA_DIR={tmp_path / 'data'}\n"
        "ICARUS_MEM0_POSTGRES_PASSWORD=p\n"
        "ICARUS_MEM0_JWT_SECRET=j\n"
        "ICARUS_MEM0_AUTH_DISABLED=true\n"
        "OPENAI_API_KEY=model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "__file__", str(script))
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="requires Docker"):
        module.main()
