"""Availability is a fixed safe-boundary notice, not an observation transport."""

import asyncio
from copy import deepcopy

from agent_app.application.session_runtime import SessionRuntime
from agent_app.contracts.prompt import PromptUpdateEvent
from icarus.core.agents import ReActAgent
from icarus.core.contracts.input import InputFinishedEvent, UserInputEvent
from icarus.core.identity import SessionIdentity
from icarus.core.llm import BaseLLM
from icarus.core.models.messages import LLMStreamChunk, ToolCall
from icarus.core.session import SessionContext
from icarus.core.tools import BaseTool, ToolExecutionResult
from icarus.core.models.messages import ToolDefinition
from apps.agent.test.agent_app.application.test_session_runtime import make_config
from apps.agent.test.agent_app.plugins.argus.test_semantics import Harness, ONE, TWO, item


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0)


def test_notification_never_steers_cancels_or_contains_observation_text_and_drains(tmp_path):
    async def run():
        started, release, query_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        signal = asyncio.Event()
        state, calls, updates, provider_inputs = {'pending': 0}, [], [], []
        initial = item()
        async def append(**values): return 1
        class Client:
            async def call(self, wake, operation, payload):
                calls.append(operation)
                if operation == 'deliver': return {'delivered_ids': payload['observation_ids']}
                if operation == 'available':
                    query_started.set()
                    return {'pending_count': state['pending'], 'watermark': 'new-watermark'}
                raise AssertionError(operation)
        context = SessionContext(kind='argus_background', scope={'source': 'qq'}, metadata={
            'protocol_version': 2, 'wake_id': 'wake', 'task_id': 'task',
            'binding': {'binding_id': 'binding', 'initial_items': [initial], 'initial_cursor': 'cursor'}})
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, 'semantic-notice'), config=make_config(tmp_path/'data'),
            context=context, publish_update=lambda value: collect(updates, value),
            argus_facilities={'client': Client(), 'append_record': append, 'availability_signal': signal})
        await runtime.start()
        class GateTool(BaseTool):
            @property
            def definition(self): return ToolDefinition('fixture_gate', 'Wait at a safe tool boundary', {'type': 'object'})
            def invoke(self, arguments, **kwargs): raise NotImplementedError
            async def ainvoke(self, arguments, **kwargs):
                started.set()
                await release.wait()
                return ToolExecutionResult(True, output='gate-complete')
        class Provider(BaseLLM):
            async def astream(self, messages, tools=None):
                provider_inputs.append(deepcopy(messages))
                if len(provider_inputs) == 1:
                    yield LLMStreamChunk(tool_calls=[ToolCall('gate', 'fixture_gate', {})], finish_reason='tool_call')
                else:
                    yield LLMStreamChunk(text_delta='done', finish_reason='stop')
            def invoke(self, messages, tools=None): raise NotImplementedError
            async def ainvoke(self, messages, tools=None): raise NotImplementedError
            def stream(self, messages, tools=None): return iter(())
            def close(self): pass
            async def aclose(self): pass
        try:
            runtime.tool_registry.unfreeze()
            runtime.tool_registry.register(GateTool())
            runtime.tool_registry.freeze()
            agent_plugin = runtime.runtime_host.get_plugin('agent')
            from icarus.core.tools import ToolExecutor
            executor = ToolExecutor(runtime.tool_registry, tracker=runtime.tool_execution_tracker)
            agent_plugin.agent_factory.get_agent = lambda role: ReActAgent(role, Provider(), executor)
            argus = runtime.runtime_host.get_plugin('argus')
            events = []
            original_publish = argus.publish
            async def publish(event):
                events.append(event)
                await original_publish(event)
            argus.publish = publish
            await runtime.submit('Investigate lunar navigation', task_id='task')
            await asyncio.wait_for(started.wait(), 3)
            assert 'deliver' in calls and 'lunar navigation' in str(provider_inputs[0])
            assert ONE not in str(provider_inputs[0])
            state['pending'] = 1
            for _ in range(10): signal.set()
            await until(lambda: any(isinstance(event, PromptUpdateEvent) for event in events))
            notices = [event for event in events if isinstance(event, PromptUpdateEvent)]
            assert len(notices) == 1
            assert all(event.task_id == 'task' for event in notices)
            assert 'argus_wait' in notices[0].sections[0].content
            assert 'NEW-SECRET-BODY' not in str(events) and TWO not in str(events)
            for _ in range(10): signal.set(); await asyncio.sleep(0)
            await argus.on_finished('forged-input', InputFinishedEvent(task_id='task', status='completed'))
            assert argus._watcher is not None and not argus._watcher.done()
            release.set()
            await asyncio.wait_for(runtime.drain_execution(), 3)
            assert argus._watcher is None
            assert len([e for e in events if isinstance(e, PromptUpdateEvent)]) == 1
            assert len(provider_inputs) >= 2
            assert 'argus_wait' in str(provider_inputs[-1])
            assert 'NEW-SECRET-BODY' not in str(provider_inputs) and TWO not in str(provider_inputs)
            assert runtime.execution_exit_kind('task') == 'completed'
            assert not any(update.type == 'task.steered' for update in updates)
        finally:
            release.set()
            await runtime.stop('test')
    asyncio.run(run())


async def collect(values, value): values.append(value)


def test_available_initial_count_is_not_queried_until_final_context_ack():
    from apps.agent.test.agent_app.plugins.argus.test_semantics import ready
    async def run():
        harness = Harness()
        plugin = harness.plugin()
        plugin.availability_signal = asyncio.Event()
        tasks = []
        def starter(name, operation):
            task = asyncio.create_task(operation(), name=name)
            tasks.append(task)
            return task
        plugin.bind_background_work_starter(starter)
        original, queried = harness.call, []
        async def call(wake, operation, payload):
            if operation == 'available':
                queried.append(True)
                return {'pending_count': 0, 'watermark': None}
            return await original(wake, operation, payload)
        harness.call = call
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        for _ in range(5): await asyncio.sleep(0)
        assert not queried, 'Do not race an availability snapshot of unacknowledged initial work'
        await plugin.on_context_ready(plugin.context_source, ready(plugin, harness.events[-1].sections[0].content))
        await until(lambda: bool(queried))
        assert not [event for event in harness.events if isinstance(event, PromptUpdateEvent)]
        await plugin.on_finished(plugin.input_source, InputFinishedEvent(task_id='task', status='completed'))
        assert all(task.done() for task in tasks)
    asyncio.run(run())


def test_input_finished_cancels_and_joins_inflight_availability_query(tmp_path):
    async def run():
        signal, querying, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        harness = Harness()
        plugin = harness.plugin()
        plugin.availability_signal = signal
        original = harness.call
        async def call(wake, operation, payload):
            if operation != 'available': return await original(wake, operation, payload)
            querying.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        harness.call = call
        tasks = []
        def starter(name, operation):
            task = asyncio.create_task(operation(), name=name)
            tasks.append(task)
            return task
        plugin.bind_background_work_starter(starter)
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        from apps.agent.test.agent_app.plugins.argus.test_semantics import ready
        await plugin.on_context_ready(plugin.context_source, ready(plugin, harness.events[-1].sections[0].content))
        await asyncio.wait_for(querying.wait(), 1)
        await plugin.on_finished(plugin.input_source, InputFinishedEvent(task_id='task', status='completed'))
        assert cancelled.is_set() and all(task.done() for task in tasks)
        assert not [event for event in harness.events if isinstance(event, PromptUpdateEvent)]
    asyncio.run(run())
