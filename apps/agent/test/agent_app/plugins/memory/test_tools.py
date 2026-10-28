from agent_app.plugins.memory.models import (
    MemoryItem, MemoryRecallResult, MemoryRecord,
)
from agent_app.plugins.memory.plugin import MemoryPlugin
from agent_app.plugins.memory.tools import create_memory_tools

from .test_plugin import BackendStub, plugin, record


def tools_with_backend(*, top_k=3, threshold=0.25, max_context_chars=6000):
    backend = BackendStub([MemoryItem("memory:1", "fact", 0.9, "global")])
    backend.records["memory:1"] = record()
    target = plugin(
        backend, top_k=top_k, threshold=threshold,
        max_context_chars=max_context_chars,
    )
    return backend, target, {tool.definition.name: tool for tool in create_memory_tools(target)}


def test_memory工具名称和并行策略固定():
    _, _, tools = tools_with_backend()
    assert list(tools) == [
        "memory_recall", "memory_read", "memory_remember",
        "memory_correct", "memory_set_recall", "memory_delete",
    ]
    assert tools["memory_recall"].can_run_parallel({}) is True
    assert tools["memory_read"].can_run_parallel({}) is True
    assert tools["memory_remember"].can_run_parallel({}) is False
    assert tools["memory_correct"].can_run_parallel({}) is False
    assert tools["memory_set_recall"].can_run_parallel({}) is False
    assert tools["memory_delete"].can_run_parallel({}) is False


def test_memory_recall空结果说明保留在工具描述中():
    _, _, tools = tools_with_backend()
    description = tools["memory_recall"].definition.description
    assert "does not prove that no memory exists" in description


def test_memory_recall参数都有描述且继承插件配置():
    backend, _, tools = tools_with_backend(
        top_k=7, threshold=0.35, max_context_chars=9000,
    )
    properties = tools["memory_recall"].definition.input_schema["properties"]
    for name in (
        "query", "is_workspace", "include_stopped",
        "top_k", "threshold", "max_context_chars",
    ):
        assert properties[name]["description"].strip()
    assert properties["is_workspace"]["default"] is None
    assert properties["include_stopped"]["default"] is False
    assert properties["top_k"]["default"] == 7
    assert properties["threshold"]["default"] == 0.35
    assert properties["max_context_chars"]["default"] == 9000

    result = tools["memory_recall"].invoke({"query": "q"})
    assert result.success is True
    assert backend.calls[-1][2]["top_k"] == 7
    assert backend.calls[-1][2]["threshold"] == 0.35


def test_memory_read按ref读取并可选返回历史():
    _, _, tools = tools_with_backend()
    current = tools["memory_read"].invoke({"ref": "memory:1"})
    assert current.success is True
    assert current.output["content"] == "remembered"

    with_history = tools["memory_read"].invoke(
        {"ref": "memory:1", "include_history": True}
    )
    assert with_history.success is True
    assert with_history.output["ref"] == "memory:1"
    assert with_history.output["memory"]["content"] == "remembered"
    assert with_history.output["history"][0]["operation"] == "UPDATE"


def test_memory_remember工具说明允许自主保存长期信息():
    _, _, tools = tools_with_backend()
    description = tools["memory_remember"].definition.description.lower()
    assert "stable fact" in description
    assert "original wording" in description
    assert "do not summarize" in description


def test_memory_remember要求原文并且scope可选默认全局():
    _, _, tools = tools_with_backend()
    definition = tools["memory_remember"].definition
    assert definition.input_schema["required"] == ["content"]
    properties = definition.input_schema["properties"]
    assert properties["is_workspace"]["default"] is False
    assert "stable fact" in definition.description
    assert "original wording" in definition.description
    assert "summarize or paraphrase" in definition.description
    assert "project (workspace)" in properties["is_workspace"]["description"]
    assert "do not summarize" in properties["content"]["description"]


def test_memory_remember省略scope时写入全局():
    backend, _, tools = tools_with_backend()
    result = tools["memory_remember"].invoke({"content": "raw"}, run_id="run")
    assert result.success is True
    assert backend.calls[-1][2]["scope"] == "global"


def test_memory_remember显式scope并传运行上下文():
    backend, _, tools = tools_with_backend()
    result = tools["memory_remember"].invoke(
        {"content": "remember", "is_workspace": False},
        task_id="task", run_id="run", step=2,
    )
    assert result.success is True
    call = backend.calls[-1]
    assert call[2]["scope"] == "global"
    assert call[2]["metadata"]["source_run_id"] == "run"
    assert call[2]["metadata"]["source_session_id"] == "s"


def test_memory_recall布尔范围和停止引用参数():
    backend, _, tools = tools_with_backend()
    result = tools["memory_recall"].invoke(
        {
            "query": "q", "is_workspace": True,
            "include_stopped": True, "max_context_chars": 800,
        }
    )
    assert result.success is True
    call = backend.calls[-1]
    assert call[2]["scope"] == "workspace"
    assert call[2]["include_stopped"] is True


def test_memory_set_recall停止与恢复():
    backend, _, tools = tools_with_backend()
    stopped = tools["memory_set_recall"].invoke(
        {"ref": "memory:1", "enabled": False}
    )
    restored = tools["memory_set_recall"].invoke(
        {"ref": "memory:1", "enabled": True}
    )
    assert stopped.success is True and restored.success is True
    assert ("expiration", "memory:1", "1970-01-01") in backend.calls
    assert ("expiration", "memory:1", None) in backend.calls


def test_memory维护工具调用正确方法且删除不是purge():
    backend, _, tools = tools_with_backend()
    assert tools["memory_correct"].invoke(
        {"ref": "memory:1", "content": "fixed"}
    ).success
    assert tools["memory_set_recall"].invoke(
        {"ref": "memory:1", "enabled": False}
    ).success
    assert tools["memory_set_recall"].invoke(
        {"ref": "memory:1", "enabled": True}
    ).success
    deleted = tools["memory_delete"].invoke({"ref": "memory:1"})
    assert deleted.output["purged"] is False
    assert any(call[0] == "delete" for call in backend.calls)


def test_memory_delete说明保留历史且不是purge():
    _, _, tools = tools_with_backend()
    description = tools["memory_delete"].definition.description
    assert "History is retained" in description
    assert "not a purge" in description


def test_memory工具拒绝其他用户workspace和agent的记忆():
    backend, _, tools = tools_with_backend()
    backend.records["memory:user"] = record("memory:user", user="other")
    backend.records["memory:ws"] = record("memory:ws", run="workspace:other")
    backend.records["memory:agent"] = record("memory:agent", agent="other")
    for ref in ("memory:user", "memory:ws", "memory:agent"):
        for name, arguments in (
            ("memory_read", {"ref": ref}),
            ("memory_correct", {"ref": ref, "content": "fixed"}),
            ("memory_set_recall", {"ref": ref, "enabled": False}),
            ("memory_delete", {"ref": ref}),
        ):
            result = tools[name].invoke(arguments)
            assert result.success is False
            assert "does not belong" in result.error


def test_memory工具拒绝未知错误类型和错误归属():
    backend, _, tools = tools_with_backend()
    assert tools["memory_recall"].invoke(
        {"query": "q", "top_k": True}
    ).success is False
    assert tools["memory_read"].invoke(
        {"ref": "memory:1", "include_history": "yes"}
    ).success is False
    assert tools["memory_remember"].invoke(
        {"content": "x", "is_workspace": "yes"}
    ).success is False
    assert tools["memory_set_recall"].invoke(
        {"ref": "memory:1", "enabled": "yes"}
    ).success is False
    backend.records["memory:other"] = record("memory:other", user="other")
    other = tools["memory_read"].invoke({"ref": "memory:other"})
    assert other.success is False
    assert "does not belong" in other.error
    corrected = tools["memory_correct"].invoke(
        {"ref": "memory:other", "content": "fixed"}
    )
    assert corrected.success is False
    assert "does not belong" in corrected.error
    result = tools["memory_delete"].invoke({"ref": "memory:other"})
    assert result.success is False
    assert "does not belong" in result.error
