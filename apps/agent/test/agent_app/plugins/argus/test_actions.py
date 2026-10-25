"""Only explicit normalized business evidence can confirm an external action."""

import asyncio

from icarus.core.agents import AgentToolCompletedEvent
from icarus.core.models.messages import ToolCall
from icarus.core.contracts.input import UserInputEvent
from icarus.core.tools import ToolExecutionResult
from apps.agent.test.agent_app.plugins.argus.test_semantics import Harness


def test_generic_mcp_success_and_onebot_retcode_are_unconfirmed_not_business_success():
    async def run():
        harness = Harness()
        plugin = harness.plugin()
        plugin.session_id = 'semantic'
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        event = AgentToolCompletedEvent(task_id='task', step=1,
            tool_call=ToolCall('send', 'mcp_tool_execute', {'tool_ref': 'snowluma/invoke_action',
                'arguments': {'action': 'send_group_msg', 'params': {'group_id': '9', 'message': 'secret request'}}}),
            result=ToolExecutionResult(True, output={'structured_content': {'status': 'ok', 'retcode': 0,
                                                                          'data': {'message_id': '123'}}}))
        await plugin.on_tool_completed('forged-agent', event)
        assert not [row for row in harness.rows if row.update_type == 'argus_action']
        await plugin.on_tool_completed(plugin.agent_source, event)
        await plugin.on_tool_completed(plugin.agent_source, event)
        actions = [row.payload for row in harness.rows if row.update_type == 'argus_action']
        assert len(actions) == 1
        assert actions[0]['business_status'] == 'unconfirmed'
        assert actions[0]['tool_call_id'] == 'send' and actions[0]['session_id'] == 'semantic'
        assert 'secret request' not in str(actions[0])
    asyncio.run(run())


def test_explicit_business_receipt_success_and_failure_are_validated_and_restored_deduped():
    async def run():
        harness = Harness()
        for success in (False, True):
            plugin = harness.plugin()
            await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
            receipt = {'kind': 'business_action_receipt', 'version': 1, 'success': success,
                       'target': {'source': 'fixture', 'chat_id': 'group:9'}, 'message_id': 'receipt-1'}
            event = AgentToolCompletedEvent(task_id='task', step=1,
                tool_call=ToolCall('receipt-' + str(success), 'mcp_tool_execute', {'tool_ref': 'fixture/send'}),
                result=ToolExecutionResult(True, output={'structured_content': {'business_receipt': receipt}}))
            await plugin.on_tool_completed(plugin.agent_source, event)
            restored = harness.plugin()
            await restored.on_input(restored.input_source, UserInputEvent(task_id='task', prompt='work'))
            await restored.on_tool_completed(restored.agent_source, event)
        actions = [row.payload for row in harness.rows if row.update_type == 'argus_action']
        assert [value['business_status'] for value in actions] == ['failed', 'confirmed']
        assert all(value['receipt']['kind'] == 'business_action_receipt' for value in actions)
    asyncio.run(run())
