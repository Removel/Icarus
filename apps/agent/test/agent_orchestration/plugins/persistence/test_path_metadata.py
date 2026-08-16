import json

import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import (
    DataPathResolver,
    MetadataStore,
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


def test_metadata_store_创建并更新workspace和session(tmp_path):
    resolver = DataPathResolver(tmp_path)
    store = MetadataStore(resolver)
    identity = SessionIdentity.create(
        tmp_path / "workspace",
        session_id="session-1",
    )

    store.initialize(identity)
    store.update_session_status(identity, "closed")

    workspace = json.loads(
        resolver.workspace_metadata(identity).read_text(encoding="utf-8")
    )
    session = json.loads(
        resolver.session_metadata(identity).read_text(encoding="utf-8")
    )
    assert workspace["workspace_path"] == str(identity.workspace_path)
    assert workspace["workspace_key"] == identity.workspace_key
    assert session["session_id"] == "session-1"
    assert session["status"] == "closed"
