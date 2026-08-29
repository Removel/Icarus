from pathlib import Path

import pytest

from apps.agent.src.application.resource_ref import ResourceRef


def test_resource_ref只解析受控目录内文件(tmp_path):
    incoming = tmp_path / "incoming"
    target = incoming / "client" / "image.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"image")

    assert ResourceRef("client/image.png").resolve(incoming) == target


@pytest.mark.parametrize(
    "resource_id",
    ["", "../secret", "/tmp/file", "client/../file", "file://x"],
)
def test_resource_ref拒绝不安全标识(resource_id):
    with pytest.raises(ValueError, match="resource_id"):
        ResourceRef(resource_id)


def test_resource_ref缺失文件明确失败(tmp_path):
    with pytest.raises(FileNotFoundError, match="unavailable"):
        ResourceRef("missing.png").resolve(Path(tmp_path))
