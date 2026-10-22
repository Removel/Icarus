"""Argus tools inject identities and call IDs rather than accepting them from the model."""

import asyncio

from icarus.core.models.messages import ToolCall
from icarus.core.tools import ToolExecutor, ToolRegistry


def test_argus_wait_receives_trusted_call_id_and_rejects_scope_override():
    async def run():
        from agent_app.plugins.argus.tools import ArgusTool

        calls = []
        class Plugin:
            _loop_runner = None
            async def execute(self, operation, arguments, **execution):
                from icarus.core.tools import ToolExecutionResult
                calls.append((operation, arguments, execution))
                return ToolExecutionResult(True, output={'items': []})

        registry = ToolRegistry()
        tool = ArgusTool(Plugin(), 'wait')
        registry.register(tool)
        assert 'binding_id' not in tool.definition.input_schema['properties']
        result = await ToolExecutor(registry).aexecute(ToolCall('trusted-call', 'argus_wait', {'limit': 1}), task_id='task')
        assert result.success
        assert calls == [('wait', {'limit': 1}, {'task_id': 'task', 'tool_call_id': 'trusted-call'})]
        bad = await tool.ainvoke({'group_id': 'other'}, task_id='task', tool_call_id='bad')
        assert not bad.success
        assert len(calls) == 1

    asyncio.run(run())


def test_sync_and_async_tools_share_existing_runtime_loop_backend():
    from icarus.runtime.loop_runner import RuntimeLoopRunner
    from icarus.core.tools import ToolExecutionResult
    from agent_app.plugins.argus.tools import ArgusTool
    async def run():
        loop = asyncio.get_running_loop()
        loops = []
        class Plugin:
            _loop_runner = RuntimeLoopRunner()
            async def execute(self, operation, arguments, **execution):
                loops.append(asyncio.get_running_loop())
                return ToolExecutionResult(True, output={'text': 'same result'})
        plugin = Plugin()
        plugin._loop_runner.bind()
        tool = ArgusTool(plugin, 'read')
        args = {'observation_id':'60000000-0000-4000-8000-000000000001'}
        direct = await tool.ainvoke(args, task_id='task', tool_call_id='async')
        bridged = await asyncio.to_thread(tool.invoke, args, task_id='task', tool_call_id='sync')
        assert direct.output == bridged.output == {'text':'same result'}
        assert loops == [loop, loop]
        plugin._loop_runner.close()
    asyncio.run(run())
