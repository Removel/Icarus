from apps.agent.src.agent_orchestration.plugins.knowledge.plugin import KnowledgePlugin
from apps.agent.src.agent_orchestration.plugins.knowledge.models import (
    KnowledgeCatalog,
    KnowledgeDocument,
)
from apps.agent.src.agent_orchestration.plugins.knowledge.tools import (
    create_knowledge_tools,
)

from .helpers import KnowledgeBackendStub


def make_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = KnowledgeBackendStub()
    plugin = KnowledgePlugin("knowledge", backend, workspace_path=workspace)
    return backend, {tool.definition.name: tool for tool in create_knowledge_tools(plugin)}


def test_knowledge只暴露五个固定工具且backend没有删除能力(tmp_path):
    backend, tools = make_tools(tmp_path)
    assert set(tools) == {
        "knowledge_query", "knowledge_list", "knowledge_read",
        "knowledge_upload", "knowledge_recompile",
    }
    assert not hasattr(backend, "delete")
    assert not hasattr(backend, "request")
    assert tools["knowledge_query"].can_run_parallel({}) is True
    assert tools["knowledge_list"].can_run_parallel({}) is True
    assert tools["knowledge_read"].can_run_parallel({}) is True
    assert tools["knowledge_upload"].can_run_parallel({}) is False
    assert tools["knowledge_recompile"].can_run_parallel({}) is False


def test_knowledge工具调用插件并拒绝未知和错误类型(tmp_path):
    backend, tools = make_tools(tmp_path)
    source = tmp_path / "workspace" / "guide.md"
    source.write_text("guide", encoding="utf-8")
    assert tools["knowledge_query"].invoke({"question": "q"}).success
    assert tools["knowledge_list"].invoke({}).success
    assert tools["knowledge_read"].invoke({"path": "summaries/guide"}).success
    assert tools["knowledge_upload"].invoke({"paths": ["guide.md"]}).success
    assert tools["knowledge_recompile"].invoke({"document": "guide"}).success
    assert tools["knowledge_query"].invoke({"question": "q", "extra": 1}).success is False
    assert tools["knowledge_upload"].invoke({"paths": "guide.md"}).success is False
    assert tools["knowledge_recompile"].invoke({}).success is False
    assert [call[0] for call in backend.calls[:5]] == [
        "query", "list", "read", "upload", "recompile"
    ]


def test_knowledge_list只在agent侧对全量catalog分页(tmp_path):
    backend, tools = make_tools(tmp_path)
    backend.catalog = KnowledgeCatalog(
        tuple(
            KnowledgeDocument(f"ref-{index}", f"doc-{index}", "md", "doc")
            for index in range(3)
        ),
        ("summaries/one", "summaries/two"),
        ("concepts/one",),
        (),
        (),
    )

    first = tools["knowledge_list"].invoke({"page_size": 4}).output
    second = tools["knowledge_list"].invoke(
        {"page_num": 2, "page_size": 4}
    ).output

    assert [item["name"] for item in first["documents"]] == [
        "doc-0", "doc-1", "doc-2"
    ]
    assert first["summaries"] == ["summaries/one"]
    assert first["pagination"] == {
        "page_num": 1,
        "page_size": 4,
        "has_more": True,
        "next_page_num": 2,
        "total_count": 6,
        "totals": {
            "documents": 3,
            "summaries": 2,
            "concepts": 1,
            "entities": 0,
            "reports": 0,
        },
    }
    assert second["documents"] == []
    assert second["summaries"] == ["summaries/two"]
    assert second["concepts"] == ["concepts/one"]
    assert second["pagination"]["has_more"] is False
    assert backend.calls == [("list",), ("list",)]


def test_knowledge_list拒绝无界或非法分页参数(tmp_path):
    _, tools = make_tools(tmp_path)

    for arguments in (
        {"page_num": 0},
        {"page_size": 0},
        {"page_size": 201},
        {"page_size": True},
        {"all": True},
    ):
        assert tools["knowledge_list"].invoke(arguments).success is False
