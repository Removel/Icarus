from pathlib import Path

from apps.tui.src.configs.config import build_config


def test_build_config_resolves_workspace_and_resource_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    config = build_config(
        cwd=workspace,
        gateway_url="ws://127.0.0.1:9999/rpc",
        session_id="demo",
        data_dir=data_dir,
    )
    assert config.workspace_path == workspace.resolve()
    assert config.resource_root == (data_dir / "incoming").resolve()
    assert config.session_id == "demo"
    assert config.gateway_url == "ws://127.0.0.1:9999/rpc"


def test_build_config_keeps_default_url(tmp_path: Path) -> None:
    config = build_config(
        cwd=tmp_path,
        gateway_url="ws://127.0.0.1:8765/rpc",
        session_id=None,
        data_dir=tmp_path / "data",
    )
    assert config.session_id is None
    assert config.gateway_url == "ws://127.0.0.1:8765/rpc"
