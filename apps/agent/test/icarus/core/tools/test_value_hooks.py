"""Argument hooks run once before batching; result hooks stay inside budgets."""

import asyncio
from dataclasses import replace

from icarus.core.hooks import BaseHook, HookDispatcher, HookRegistry
from icarus.core.hooks.wrappers import ObservableToolExecutor
from icarus.core.models.messages import ToolCall, ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutor, ToolExecutionResult, ToolRegistry
from icarus.core.tools.result_budget import conservative_token_count, serialize_tool_result


class ExampleTool(BaseTool):
    @property
    def definition(self):
        return ToolDefinition("example", "example", {"type": "object"})

    def invoke(self, arguments):
        return ToolExecutionResult(True, arguments)


def test_argument_rewrite_precedes_parallel_check_and_runs_once():
    calls = []
    parallel = []
    class Tool(ExampleTool):
        def can_run_parallel(self, arguments):
            parallel.append(arguments["parallel"])
            return arguments["parallel"]
    class Rewrite(BaseHook):
        def handle(self, event, value):
            calls.append(value.id)
            return replace(value, arguments={"parallel": True})
    tools = ToolRegistry(); tools.register(Tool())
    hooks = HookRegistry(); hooks.register("tool.execute", Rewrite(), priority=1)
    executor = ObservableToolExecutor(ToolExecutor(tools), HookDispatcher(hooks))
    results = executor.execute_many([ToolCall("one", "example", {"parallel": False})])
    assert parallel == [True]
    assert calls == ["one"]
    assert results[0][1].output == {"parallel": True}


def test_async_result_hook_runs_before_final_output_budget():
    async def run():
        visited = []
        class Rewrite(BaseHook):
            def handle(self, event, value):
                return value
            async def ahandle(self, event, value):
                if event.phase == "after":
                    visited.append(event.phase)
                    return replace(value, output="enlarged " * 20000)
                return value
        tools = ToolRegistry(); tools.register(ExampleTool())
        hooks = HookRegistry(); hooks.register("tool.execute", Rewrite(), priority=1)
        executor = ObservableToolExecutor(ToolExecutor(tools), HookDispatcher(hooks))
        result = await executor.aexecute_many([ToolCall("one", "example", {})])
        assert visited == ["after"]
        assert conservative_token_count(serialize_tool_result(result[0][1])) <= 4000
        assert result[0][1].metadata["result_truncated"] is True
    asyncio.run(run())
