import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import (
    ImageAssetError,
    PersistenceRuntime,
    PersistenceSession,
    SessionIdentity,
)
from apps.agent.src.model_provider.types import ImagePart


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def make_session(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = PersistenceRuntime(tmp_path / "data", workspace)
    identity = SessionIdentity.create(workspace, "session-1")
    return PersistenceSession(runtime, identity)


def test_import_image按内容稳定存储且不依赖原路径(tmp_path):
    session = make_session(tmp_path)
    source = tmp_path / "picture.any"
    source.write_bytes(PNG)

    first = session.import_image(source)
    second = session.import_image(source)
    source.unlink()

    assert first == second
    assert first.source_type == "asset"
    assert first.source.startswith("assets/")
    assert first.source.endswith(".png")
    assert first.media_type == "image/png"
    assert session.resolve_image(first).read_bytes() == PNG


def test_resolve_image拒绝非asset和路径逃逸(tmp_path):
    session = make_session(tmp_path)

    with pytest.raises(ImageAssetError, match="not a session asset"):
        session.resolve_image(ImagePart("https://example.com/a.png"))
    with pytest.raises(ImageAssetError, match="invalid"):
        session.resolve_image(ImagePart("../a.png", "asset"))
    with pytest.raises(ImageAssetError, match="escapes"):
        session.resolve_image(ImagePart("assets/../a.png", "asset"))


def test_import_image拒绝缺失和不支持格式且不暴露路径(tmp_path):
    session = make_session(tmp_path)
    missing = tmp_path / "secret-location.png"
    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image", encoding="utf-8")

    with pytest.raises(ImageAssetError) as missing_error:
        session.import_image(missing)
    assert str(missing) not in str(missing_error.value)
    with pytest.raises(ImageAssetError, match="unsupported"):
        session.import_image(invalid)
