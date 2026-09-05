import pytest

from apps.agent.src.agent_orchestration.plugins.mcp.config import (
    parse_mcp_servers,
)


def test_parse_mcp_servers支持常见stdio和http配置():
    servers = parse_mcp_servers(
        {
            "browser": {
                "command": "npx",
                "args": ["-y", "browser-mcp"],
                "customField": "kept",
            },
            "blender": {
                "url": "http://127.0.0.1:9876/mcp",
                "transport": "streamable-http",
            },
            "disabled": {"enabled": False, "command": "ignored"},
        }
    )

    assert [(item.name, item.transport) for item in servers] == [
        ("browser", "stdio"),
        ("blender", "streamable-http"),
    ]
    assert servers[0].raw["customField"] == "kept"
    assert "enabled" not in servers[0].raw


@pytest.mark.parametrize(
    "value",
    [
        {"bad": {}},
        {"bad": {"command": "x", "url": "https://x"}},
        {"bad/name": {"command": "x"}},
        {" bad ": {"command": "x"}},
        {"bad": {"command": "x", "enabled": "true"}},
        {"bad": {"url": "https://x", "transport": "stdio"}},
        {
            "bad": {
                "command": "x", "transport": "stdio", "type": "http"
            }
        },
    ],
)
def test_parse_mcp_servers拒绝不明确配置(value):
    with pytest.raises(ValueError):
        parse_mcp_servers(value)


def test_server_config只在连接前展开环境并补workspace(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "secret")
    server = parse_mcp_servers(
        {
            "local": {
                "command": "runner",
                "env": {"TOKEN": "${MCP_TOKEN}"},
            }
        }
    )[0]

    assert server.raw["env"]["TOKEN"] == "${MCP_TOKEN}"
    assert server.resolved(workspace_path="/workspace") == {
        "command": "runner",
        "env": {"TOKEN": "secret"},
        "cwd": "/workspace",
        "transport": "stdio",
    }


def test_http配置固定使用streamable_http而不按url猜测sse():
    server = parse_mcp_servers(
        {"remote": {"url": "https://example.com/sse"}}
    )[0]

    assert server.resolved(workspace_path="/workspace")["transport"] == (
        "streamable-http"
    )


def test_server_config缺少环境变量时不猜测():
    server = parse_mcp_servers(
        {"remote": {"url": "https://example.com/${MISSING_MCP_TOKEN}"}}
    )[0]

    with pytest.raises(ValueError, match="MISSING_MCP_TOKEN"):
        server.resolved(workspace_path="/workspace")
