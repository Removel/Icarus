from apps.agent.src.agent_orchestration.plugins.blackboard import (
    BlackboardListTool,
    BlackboardReadTool,
    RegionDefinition,
    RegionOutput,
    RegionRegistry,
    RegionState,
    RegionStore,
)


def make_store():
    registry = RegionRegistry()
    registry.register(
        RegionDefinition(
            region="memory",
            owner_plugin_id="memory",
            lifetime="input",
            required_for_start=True,
            auto_expose=True,
            allowed_statuses=("idle", "recalling"),
            max_data_chars=500,
        )
    )
    registry.freeze()
    store = RegionStore(registry)
    store.begin_input("input-1")
    store.apply(
        source_plugin_id="memory", task_id="task-1",
        current_task_id="task-1", region="memory", input_id="input-1",
        input_value=None,
        output=RegionOutput(
            summary="two hits",
            refs=("memory:1", "memory:2"),
            data={
                "items": [
                    {"ref": "memory:1", "content": "first"},
                    {"ref": "memory:2", "content": "second"},
                ],
                "source": "automatic",
            },
        ),
        state=RegionState("idle"), complete_for_input=True,
    )
    return store


def test_blackboard_list只返回紧凑当前状态():
    result = BlackboardListTool(make_store()).invoke({})
    assert result.success is True
    assert result.output["regions"] == [
        {
            "region": "memory",
            "owner_plugin_id": "memory",
            "lifetime": "input",
            "status": "idle",
            "summary": "two hits",
            "refs": ["memory:1", "memory:2"],
            "fresh_for_current_input": True,
            "updated_at": result.output["regions"][0]["updated_at"],
        }
    ]


def test_blackboard_read按ref过滤本地items且不改其他字段():
    result = BlackboardReadTool(make_store()).invoke(
        {"region": "memory", "sections": ["output"], "refs": ["memory:2"]}
    )
    assert result.success is True
    output = result.output["sections"]["output"]
    assert output["summary"] == "two hits"
    assert output["refs"] == ["memory:2"]
    assert output["data"] == {
        "items": [{"ref": "memory:2", "content": "second"}],
        "source": "automatic",
    }


def test_blackboard_read只能缩小预算并报告截断():
    result = BlackboardReadTool(make_store()).invoke(
        {"region": "memory", "sections": ["output"], "limit_chars": 20}
    )
    assert result.success is True
    assert result.output["truncated"] is True
    import json
    assert len(
        json.dumps(
            result.output["sections"]["output"]["data"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    ) <= 20


def test_blackboard工具拒绝写入和未知参数():
    store = make_store()
    assert BlackboardListTool(store).invoke({"write": True}).success is False
    assert BlackboardReadTool(store).invoke(
        {"region": "memory", "patch": {}}
    ).success is False
