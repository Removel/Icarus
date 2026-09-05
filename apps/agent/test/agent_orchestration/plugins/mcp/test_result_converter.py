import base64

import pytest

from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPCallResult,
    MCPContent,
)
from apps.agent.src.agent_orchestration.plugins.mcp.result_converter import (
    MCPResultConverter,
)
from apps.agent.src.agent_orchestration.plugins.persistence import (
    PersistenceRuntime,
    PersistenceSession,
    SessionIdentity,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"mcp-image"


def make_converter(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = PersistenceRuntime(tmp_path / "data", workspace)
    identity = SessionIdentity.create(workspace, "session-1")
    return MCPResultConverter(PersistenceSession(runtime, identity))


def test_converter保留文本结构化内容并脱敏metadata(tmp_path):
    converter = make_converter(tmp_path)
    result = converter.convert(
        MCPCallResult(
            content=(MCPContent("text", "created"),),
            structured_content={"count": 3},
            metadata={"token": "secret", "source": "blender"},
        )
    )

    assert result.success is True
    assert result.output == {
        "content": [{"type": "text", "text": "created"}],
        "structured_content": {"count": 3},
        "metadata": {"token": "[REDACTED]", "source": "blender"},
    }


def test_converter将mcp图片保存为session_asset(tmp_path):
    converter = make_converter(tmp_path)
    result = converter.convert(
        MCPCallResult(
            content=(
                MCPContent(
                    "image",
                    base64.b64encode(PNG).decode("ascii"),
                    media_type="image/png",
                ),
            )
        )
    )

    assert result.success is True
    assert len(result.images) == 1
    assert result.images[0].source.startswith("assets/")
    assert "mcp-image" not in str(result.as_dict())


def test_converter拒绝无效base64且不静默丢弃audio(tmp_path):
    converter = make_converter(tmp_path)
    with pytest.raises(ValueError, match="base64"):
        converter.convert(
            MCPCallResult(content=(MCPContent("image", "not base64!"),))
        )

    result = converter.convert(
        MCPCallResult(
            content=(MCPContent("audio", "ignored", media_type="audio/wav"),)
        )
    )
    assert result.output["content"] == [
        {"type": "audio", "media_type": "audio/wav", "unsupported": True}
    ]


def test_converter在base64解码前拒绝超限图片(tmp_path):
    converter = make_converter(tmp_path)
    oversized = "A" * (((20 * 1024 * 1024 + 2) // 3) * 4 + 1)

    with pytest.raises(ValueError, match="maximum"):
        converter.convert(
            MCPCallResult(
                content=(MCPContent("image", oversized, media_type="image/png"),)
            )
        )


def test_converter限制单次结果的图片总大小(monkeypatch, tmp_path):
    import apps.agent.src.agent_orchestration.plugins.mcp.result_converter as module

    monkeypatch.setattr(module, "MAX_IMAGE_BYTES", 12)
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
    with pytest.raises(ValueError, match="images exceed"):
        make_converter(tmp_path).convert(
            MCPCallResult(
                content=(
                    MCPContent("image", encoded, media_type="image/png"),
                    MCPContent("image", encoded, media_type="image/png"),
                )
            )
        )


def test_converter限制单次结果的图片数量(monkeypatch, tmp_path):
    import apps.agent.src.agent_orchestration.plugins.mcp.result_converter as module

    monkeypatch.setattr(module, "MAX_MCP_IMAGES", 1)
    encoded = base64.b64encode(PNG).decode("ascii")
    with pytest.raises(ValueError, match="too many images"):
        make_converter(tmp_path).convert(
            MCPCallResult(
                content=(
                    MCPContent("image", encoded, media_type="image/png"),
                    MCPContent("image", encoded, media_type="image/png"),
                )
            )
        )


def test_converter脱敏embedded_resource_uri(tmp_path):
    result = make_converter(tmp_path).convert(
        MCPCallResult(
            content=(
                MCPContent(
                    "resource",
                    {
                        "uri": "https://files.example/x?access_token=top-secret",
                        "blob": base64.b64encode(PNG).decode("ascii"),
                        "mimeType": "image/png",
                    },
                ),
            )
        )
    )

    assert "top-secret" not in str(result.output)
    assert "[REDACTED]" in result.output["content"][0]["resource_uri"]


def test_converter将mcp业务错误保留为tool失败(tmp_path):
    result = make_converter(tmp_path).convert(
        MCPCallResult(
            content=(MCPContent("text", "object not found"),),
            is_error=True,
        )
    )

    assert result.success is False
    assert result.error == "object not found"


def test_converter脱敏mcp文本和业务错误(tmp_path):
    result = make_converter(tmp_path).convert(
        MCPCallResult(
            content=(
                MCPContent("text", "Authorization: Bearer top-secret failed"),
            ),
            is_error=True,
        )
    )

    assert "top-secret" not in str(result.output)
    assert "top-secret" not in result.error
    assert "[REDACTED]" in result.error


def test_converter保留任意json类型的structured_content(tmp_path):
    converter = make_converter(tmp_path)

    assert converter.convert(
        MCPCallResult(structured_content=[1, {"value": True}])
    ).output["structured_content"] == [1, {"value": True}]
    assert converter.convert(
        MCPCallResult(structured_content="plain")
    ).output["structured_content"] == "plain"
