from apps.agent.src.agent_orchestration.plugins.memory.models import (
    MemoryItem, MemoryRecallResult, MemoryRecord,
)
from apps.agent.src.agent_orchestration.plugins.memory.plugin import MemoryPlugin
from apps.agent.src.agent_orchestration.plugins.memory.tools import create_memory_tools

from .test_plugin import BackendStub, plugin, record


def tools_with_backend():
    backend = BackendStub([MemoryItem("memory:1", "fact", 0.9, "global")])
    backend.records["memory:1"] = record()
    target = plugin(backend)
    return backend, target, {tool.definition.name: tool for tool in create_memory_tools(target)}


def test_memory工具名称和并行策略固定():
    _, _, tools = tools_with_backend()
    assert list(tools) == [
        "memory_recall", "memory_get", "memory_history",
        "memory_remember", "memory_correct", "memory_stop_reference",
        "memory_restore_reference", "memory_delete",
    ]
    assert tools["memory_recall"].can_run_parallel({}) is True
    assert tools["memory_get"].can_run_parallel({}) is True
    assert tools["memory_history"].can_run_parallel({}) is True
    assert tools["memory_remember"].can_run_parallel({}) is False


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


def test_memory_remember只接受明确scope并传运行上下文():
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


def test_memory维护工具调用正确方法且删除不是purge():
    backend, _, tools = tools_with_backend()
    assert tools["memory_get"].invoke({"ref": "memory:1"}).success
    assert tools["memory_history"].invoke({"ref": "memory:1"}).success
    assert tools["memory_correct"].invoke(
        {"ref": "memory:1", "content": "fixed"}
    ).success
    assert tools["memory_stop_reference"].invoke({"ref": "memory:1"}).success
    assert tools["memory_restore_reference"].invoke({"ref": "memory:1"}).success
    deleted = tools["memory_delete"].invoke({"ref": "memory:1"})
    assert deleted.output["purged"] is False
    assert any(call[0] == "delete" for call in backend.calls)


def test_memory工具拒绝未知错误类型和错误归属():
    backend, _, tools = tools_with_backend()
    assert tools["memory_recall"].invoke({"query": "q", "top_k": True}).success is False
    assert tools["memory_remember"].invoke({"content": "x", "is_workspace": "yes"}).success is False
    assert tools["memory_get"].invoke({"ref": " "}).success is False
    backend.records["memory:other"] = record("memory:other", user="other")
    result = tools["memory_delete"].invoke({"ref": "memory:other"})
    assert result.success is False
    assert "does not belong" in result.error
