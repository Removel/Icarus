import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import (
    DataPathResolver,
    JsonStateStore,
    SessionIdentity,
)


def test_path_resolver_按workspace和session生成安全目录(tmp_path):
    resolver = DataPathResolver(tmp_path)
    identity = SessionIdentity.create(
        tmp_path / "workspace",
        session_id="session-1",
    )

    resolver.ensure_session(identity)

    assert resolver.trace_file(identity).parent == resolver.session_dir(identity)
    assert resolver.assets_dir(identity).is_dir()
    assert resolver.workspace_log(identity).parent == resolver.workspace_dir(identity)
    assert resolver.global_skills_dir == tmp_path / "skills"


def test_path_resolver_拒绝相对路径和路径穿越(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        DataPathResolver("relative")

    resolver = DataPathResolver(tmp_path)
    identity = SessionIdentity.create(
        tmp_path / "workspace",
        session_id="../escape",
    )
    with pytest.raises(ValueError, match="unsafe"):
        resolver.session_dir(identity)


def test_json_state_store原子读写plugin状态(tmp_path):
    resolver = DataPathResolver(tmp_path)
    store = JsonStateStore()
    identity = SessionIdentity.create(
        tmp_path / "workspace",
        session_id="session-1",
    )
    path = resolver.session_dir(identity) / "plugin-state" / "plugin.json"

    store.write(path, {"state_version": 1, "state": {"value": 1}})
    first = store.read(path)
    store.write(path, {"state_version": 1, "state": {"value": 2}})

    assert first == {"state_version": 1, "state": {"value": 1}}
    assert store.read(path) == {
        "state_version": 1,
        "state": {"value": 2},
    }
    assert not tuple(path.parent.glob(".*.tmp"))
