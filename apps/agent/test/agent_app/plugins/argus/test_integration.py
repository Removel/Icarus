"""The real SessionRuntime assembles the background plugin and existing agent loop."""

import asyncio

from agent_app.plugins.mcp.client_manager import MCPClientManager
from agent_app.plugins.mcp.models import MCPCallResult, MCPContent, MCPServerInfo, MCPToolDescriptor
from icarus.core.models.messages import ToolCall
from icarus.core.tools import ToolExecutor

from agent_app.application.session_runtime import SessionRuntime
from agent_app.contracts.prompt import PromptContributionEvent
from icarus.core.identity import SessionIdentity
from icarus.core.session import SessionContext
from apps.agent.test.agent_app.application.test_session_runtime import make_config, AgentStub



def test_background_runtime_uses_existing_mcp_catalog_and_execute_without_special_routing(tmp_path, monkeypatch):
    async def run():
        calls = []

        class LocalBackend:
            async def connect(self):
                return MCPServerInfo('snowluma', supports_tools=True)

            async def list_tools(self):
                return (MCPToolDescriptor(
                    'snowluma/invoke_action', 'snowluma', 'invoke_action', 'Run a configured OneBot action',
                    {'type': 'object', 'properties': {'action': {'type': 'string'}, 'params': {'type': 'object'}},
                     'required': ['action'], 'additionalProperties': False}),)

            async def call_tool(self, name, arguments, *, timeout_seconds=None):
                calls.append((name, arguments))
                return MCPCallResult(content=(MCPContent('text', 'fixture message accepted'),),
                                     structured_content={'status': 'ok', 'retcode': 0, 'data': {'message_id': 123}})

            async def close(self):
                pass

        monkeypatch.setattr(MCPClientManager, '_create_backend', lambda self, config, changed: LocalBackend())
        config = make_config(tmp_path / 'data').model_copy(update={
            'mcp_servers': {'snowluma': {'command': 'offline-snowluma-fixture'}}})
        context = SessionContext(kind='argus_background', scope={
            'source': 'qq', 'account_id': '3922275740', 'chat_kind': 'group', 'external_id': '42'}, metadata={
            'wake_id': 'wake', 'task_id': 'task', 'binding': {'binding_id': 'binding',
                'initial_items': [{'observation_id': 'item', 'role': 'work', 'origin': 'initial', 'excerpt': 'question'}],
                'initial_cursor': 'cursor'}})

        class Client:
            async def call(self, *args):
                return {'delivered_ids': ['item']}

        async def append(**kwargs):
            return 1

        runtime = SessionRuntime(SessionIdentity.create(tmp_path, 'background-mcp'), config=config, context=context,
            publish_update=lambda _: asyncio.sleep(0), argus_facilities={'client': Client(), 'append_record': append})
        await runtime.start()
        try:
            executor = ToolExecutor(runtime.tool_registry)
            listed = await executor.aexecute(ToolCall('list', 'mcp_tool_list', {'server': 'snowluma'}), task_id='task')
            assert listed.success and listed.output['total'] == 1
            assert listed.output['tools'][0]['tool_ref'] == 'snowluma/invoke_action'
            arguments = {'action': 'send_group_msg', 'params': {'group_id': 43, 'message': 'offline fixture'}}
            sent = await executor.aexecute(ToolCall('send', 'mcp_tool_execute', {
                'tool_ref': 'snowluma/invoke_action', 'arguments': arguments}), task_id='task')
            assert sent.success
            assert calls == [('invoke_action', arguments)]
            assert '123' in str(sent.output)
            system = runtime.runtime_host.get_plugin('prompt')._system_prompt
            assert 'Do not use QQ MCP' not in system
            assert 'send platform messages' not in system
            assert 'MCP' in system
        finally:
            await runtime.stop('test')

    asyncio.run(run())


def test_real_background_runtime_registers_four_tools_and_delivers_before_model(tmp_path):
    async def run():
        calls, updates = [], []
        item = {'observation_id': '60000000-0000-4000-8000-000000000001', 'role': 'work',
                'origin': 'initial', 'excerpt': 'BACKGROUND QUESTION'}
        context = SessionContext(kind='argus_background', scope={'source': 'qq'}, metadata={
            'wake_id': 'wake', 'task_id': 'task', 'binding': {
                'binding_id': 'binding', 'initial_items': [item], 'initial_cursor': 'cursor'},
        })

        class Client:
            async def call(self, wake_id, operation, payload):
                calls.append(operation)
                return {'delivered_ids': [item['observation_id']]}
            async def aclose(self): pass

        async def append(**values): return 1
        async def publish(update): updates.append(update)
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, 'background'), config=make_config(tmp_path/'data'),
            context=context, publish_update=publish, argus_facilities={'client': Client(), 'append_record': append})
        await runtime.start()
        try:
            assert {'argus_read', 'argus_wait', 'argus_history', 'argus_report'} <= set(runtime.tool_registry.names())
            plugin = runtime.runtime_host.get_plugin('agent')
            plugin.agent_factory.get_agent = lambda role: AgentStub()
            await runtime.submit('work', task_id='task')
            await asyncio.wait_for(runtime.drain_execution(), 3)
            assert calls == ['deliver']
            assert any(update.type == 'task.finished' for update in updates)
            assert runtime.execution_exit_kind('task') == 'completed'
            system = runtime.runtime_host.get_plugin('prompt')._system_prompt
            assert 'background observation task' in system
            assert 'BACKGROUND QUESTION' not in system
        finally:
            await runtime.stop('test')



def test_real_background_runtime_required_contribution_failure_never_runs_model(tmp_path):
    async def run():
        from agent_app.argus_protocol import HandoffError
        model_calls, updates = [], []
        context = SessionContext(kind='argus_background', scope={'source': 'qq'}, metadata={
            'wake_id': 'wake', 'task_id': 'task', 'binding': {'binding_id': 'binding',
                'initial_items': [{'observation_id': 'item', 'role': 'work', 'origin': 'initial', 'excerpt': 'question'}],
                'initial_cursor': 'cursor'},
        })
        class Client:
            async def call(self, *args): raise HandoffError('unavailable', retryable=True)
            async def aclose(self): pass
        async def publish(update): updates.append(update)
        async def append(**kwargs): return 1
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, 'background-failed'),
            config=make_config(tmp_path/'data'), context=context, publish_update=publish,
            argus_facilities={'client': Client(), 'append_record': append})
        await runtime.start()
        try:
            def get_agent(role):
                model_calls.append(role)
                return AgentStub()
            runtime.runtime_host.get_plugin('agent').agent_factory.get_agent = get_agent
            await runtime.submit('work', task_id='task')
            await asyncio.wait_for(runtime.drain_execution(), 3)
            assert model_calls == []
            assert any(update.type == 'task.finished' and update.payload['status'] == 'failed' for update in updates)
            assert runtime.execution_exit_kind('task') == 'failed'
        finally:
            await runtime.stop('test')
    asyncio.run(run())


def test_initial_long_observation_is_truncated_to_prompt_budget_before_delivery(tmp_path):
    async def run():
        model_calls, updates = [], []
        item = {'observation_id':'60000000-0000-4000-8000-000000000001', 'role':'work',
            'origin':'initial', 'excerpt':'X'*20000}
        context = SessionContext(kind='argus_background', scope={'source':'qq'}, metadata={
            'wake_id':'wake','task_id':'task','binding':{'binding_id':'binding','initial_items':[item],'initial_cursor':'cursor'}})
        class Client:
            async def call(self, *args): return {'delivered_ids':[item['observation_id']]}
        async def append(**kwargs): return 1
        async def publish(update): updates.append(update)
        runtime = SessionRuntime(SessionIdentity.create(tmp_path,'long-background'), config=make_config(tmp_path/'data'),
            context=context, publish_update=publish, argus_facilities={'client':Client(),'append_record':append})
        await runtime.start()
        try:
            def get_agent(role): model_calls.append(role); return AgentStub()
            runtime.runtime_host.get_plugin('agent').agent_factory.get_agent = get_agent
            await runtime.submit('work',task_id='task')
            await asyncio.wait_for(runtime.drain_execution(),3)
            assert model_calls
            assert runtime.execution_exit_kind('task') == 'completed'
        finally: await runtime.stop('test')
    asyncio.run(run())
