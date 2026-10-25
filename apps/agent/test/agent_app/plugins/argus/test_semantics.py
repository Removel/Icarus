"""Version two model views are short, persistent, and acknowledged only after visibility."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from agent_app.plugins.argus.plugin import ArgusPlugin
from icarus.core.agents import AgentToolCompletedEvent
from icarus.core.contracts.context import BlackboardContextReadyEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.models.messages import ToolCall
from icarus.core.session import SessionContext
from icarus.core.tools import ToolExecutionResult

ONE = '60000000-0000-4000-8000-000000000001'
TWO = '60000000-0000-4000-8000-000000000002'


def item(identifier=ONE, text='Could Ada explain the lunar navigation bug?', **values):
    return dict(observation_id=identifier, role='work', origin='initial', excerpt=text,
                truncated=False, sender_id='42', sender_name='Ada', sent_at='2026-10-25T10:00:00Z',
                observed_at='2026-10-25T10:00:01Z', source='qq', chat_id='group:9',
                locator={'account_id': '7', 'chat_kind': 'group', 'external_id': '9', 'message_id': '81'}, **values)


class Harness:
    def __init__(self, *, rows=None, items=None):
        self.rows, self.events, self.calls = rows if rows is not None else [], [], []
        self.items = items or [item()]
        self.read_text = 'abcdefghij'

    def plugin(self, task='task', wake='wake', binding='binding', *, loader=None):
        context = SessionContext(kind='argus_background', scope={'source': 'qq', 'chat_id': 'group:9'}, metadata={
            'protocol_version': 2, 'task_id': task, 'wake_id': wake, 'reason': 'A navigation question needs attention',
            'binding': {'binding_id': binding, 'initial_items': self.items, 'initial_cursor': 'cursor'}})
        plugin = ArgusPlugin('argus', context=context, client=self, append_record=self.append,
                             load_records=self.load, load_binding=loader)
        plugin.bind_publisher(self.publish)
        return plugin

    async def call(self, wake, operation, payload):
        self.calls.append((wake, operation, payload))
        if operation == 'deliver':
            return {'protocol_version': 2, 'delivered_ids': payload['observation_ids']}
        if operation == 'read':
            offset = payload.get('text_offset', 0)
            end = min(len(self.read_text), offset + payload.get('max_chars', 20000))
            return {'protocol_version': 2, 'item': item(payload['observation_id'], ''),
                    'text': self.read_text[offset:end], 'next_text_offset': end if end < len(self.read_text) else None,
                    'truncated': end < len(self.read_text)}
        if operation in {'wait', 'history'}:
            return {'protocol_version': 2, 'items': self.items, 'next_cursor': 'next', 'has_more': False, 'truncated': False}
        if operation == 'report':
            return {'protocol_version': 2, 'report_id': payload['report_id'], 'disposition': 'accepted', 'report_cursor': 1}
        raise AssertionError(operation)

    async def append(self, **values):
        self.rows.append(SimpleNamespace(**values))
        return len(self.rows)

    async def load(self, update_types):
        return [row for row in self.rows if row.update_type in update_types]

    async def publish(self, event):
        self.events.append(event)


async def complete(plugin, result, call_id, operation='read', task='task'):
    await plugin.on_tool_completed(plugin.agent_source, AgentToolCompletedEvent(task_id=task, step=1,
        tool_call=ToolCall(call_id, 'argus_' + operation, {}), result=result))


def ready(plugin, content, *, task='task'):
    normalized = json.dumps([{'source_plugin_id': 'prompt', 'context_type': 'prompt',
                              'content': '## contributor/argus/observations\n' + content, 'metadata': {}}])
    return BlackboardContextReadyEvent(task_id=task, model_role='thinking', system_prompt='',
                                      input_prompt='<plugin_context>\n' + normalized + '\n</plugin_context>')


def test_prompt_preserves_sender_place_time_content_reason_and_work_without_internal_ids():
    async def run():
        harness = Harness()
        plugin = harness.plugin()
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        text = harness.events[-1].sections[0].content
        for value in ('#1', 'Ada', 'group', '2026-10-25', 'lunar navigation', 'needs attention', 'work'):
            assert value in text
        assert ONE not in text and 'binding_id' not in text and 'wake_id' not in text
        assert not harness.calls, 'Composing a contribution must not acknowledge delivery'
        schema = plugin.build_tools()[0].definition.input_schema
        assert schema['required'] == ['ref'] and 'observation_id' not in schema['properties']
    asyncio.run(run())


def test_references_survive_next_task_restart_and_never_rebind():
    async def run():
        harness = Harness()
        first = harness.plugin()
        await first.on_input(first.input_source, UserInputEvent(task_id='task', prompt='work'))
        second = harness.plugin(task='task-two', wake='wake-two', binding='binding-two')
        await second.on_input(second.input_source, UserInputEvent(task_id='task-two', prompt='work'))
        assert second.references.resolve('#1')['observation_id'] == ONE
        harness.items = [item(TWO)]
        third = harness.plugin(task='task-three', wake='wake-three', binding='binding-three')
        await third.on_input(third.input_source, UserInputEvent(task_id='task-three', prompt='work'))
        assert third.references.resolve('#2')['observation_id'] == TWO
        assert third.references.resolve('#1')['original_binding_id'] == 'binding'
        unknown = await third.execute('read', {'ref': '#99'}, task_id='task-three')
        assert unknown.error == 'unknown_reference'
        old = await third.execute('report', {'items': [{'ref': '#1', 'status': 'handled'}]}, task_id='task-three')
        assert old.error == 'not_exposed'
        assert len([r for r in harness.rows if r.update_type == 'argus_reference']) == 2
    asyncio.run(run())


def test_initial_ack_requires_trusted_final_complete_view_and_omitted_items_remain_readable():
    async def run():
        harness = Harness(items=[item(), item(TWO, 'omitted question')])
        plugin = harness.plugin()
        plugin.prompt_fits = lambda text: 'omitted question' not in text
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        content = harness.events[-1].sections[0].content
        assert not harness.calls
        await plugin.on_context_ready('forged-board', ready(plugin, content))
        assert not harness.calls
        await plugin.on_context_ready(plugin.context_source, ready(plugin, content, task='other'))
        assert not harness.calls
        await plugin.on_context_ready(plugin.context_source, ready(plugin, content))
        deliveries = [p['observation_ids'] for _, op, p in harness.calls if op == 'deliver']
        assert deliveries == [[ONE]]
        reported = await plugin.execute('report', {'items': [{'ref': '#1', 'status': 'handled'}]}, task_id='task')
        assert reported.success
        wait = await plugin.execute('wait', {}, task_id='task', tool_call_id='wait')
        assert any(value['ref'] == '#2' and 'omitted question' in value['text'] for value in wait.output['items'])
    asyncio.run(run())


def test_two_bindings_restore_only_their_own_report_identity():
    async def run():
        harness = Harness()
        original = harness.call
        async def unavailable(wake, operation, payload):
            if operation == 'report':
                from agent_app.argus_protocol import HandoffError
                raise HandoffError('unavailable', retryable=True)
            return await original(wake, operation, payload)
        harness.call = unavailable
        first = harness.plugin()
        await first.on_input(first.input_source, UserInputEvent(task_id='task', prompt='work'))
        await first.on_context_ready(first.context_source, ready(first, harness.events[-1].sections[0].content))
        arguments = {'items': [{'ref': '#1', 'status': 'handled'}]}
        assert not (await first.execute('report', arguments, task_id='task')).success
        outbox = [r for r in harness.rows if r.update_type == 'argus_report'][0]
        harness.call = original
        second = harness.plugin(task='task-two', wake='wake-two', binding='binding-two')
        await second.on_input(second.input_source, UserInputEvent(task_id='task-two', prompt='work'))
        await second.on_context_ready(second.context_source, ready(second, harness.events[-1].sections[0].content, task='task-two'))
        assert (await second.execute('report', arguments, task_id='task-two')).success
        recovered = harness.plugin()
        await recovered.on_input(recovered.input_source, UserInputEvent(task_id='task', prompt='work'))
        await recovered.on_context_ready(recovered.context_source, ready(recovered, harness.events[-1].sections[0].content))
        assert (await recovered.execute('report', arguments, task_id='task')).success
        reports = [(wake, payload) for wake, op, payload in harness.calls if op == 'report']
        assert [wake for wake, _ in reports] == ['wake-two', 'wake']
        assert reports[1][1]['report_id'] == outbox.payload['report_id']
        assert reports[0][1]['report_id'] != outbox.payload['report_id']
    asyncio.run(run())


def test_v2_all_tool_model_outputs_hide_handoff_ids_and_keep_pagination_private():
    async def run():
        harness = Harness()
        plugin = harness.plugin()
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        content = harness.events[-1].sections[0].content
        await plugin.on_context_ready(plugin.context_source, ready(plugin, content))
        for tool in plugin.build_tools():
            schema = tool.definition.input_schema
            assert 'cursor' not in schema['properties']
            assert 'observation_id' not in str(schema)
        history = await plugin.execute('history', {}, task_id='task', tool_call_id='history')
        assert '#1' in str(history.output) and ONE not in str(history.output)
        report = await plugin.execute('report', {'items': [{'ref': '#1', 'status': 'handled'}]}, task_id='task')
        assert report.output == {'disposition': 'accepted', 'report_cursor': 1}
        assert 'report_id' not in str(report.output)
    asyncio.run(run())


def test_old_reference_context_read_rechecks_active_binding_and_is_not_reportable():
    async def run():
        harness = Harness()
        first = harness.plugin()
        await first.on_input(first.input_source, UserInputEvent(task_id='task', prompt='work'))
        harness.items = [item(TWO)]
        second = harness.plugin(task='task-two', wake='wake-two', binding='binding-two')
        await second.on_input(second.input_source, UserInputEvent(task_id='task-two', prompt='work'))
        original = harness.call
        async def context_read(wake, operation, payload):
            result = await original(wake, operation, payload)
            if operation == 'read': result['item']['role'] = 'context'
            return result
        harness.call = context_read
        result = await second.execute('read', {'ref': '#1'}, task_id='task-two', tool_call_id='old')
        await complete(second, result, 'old', task='task-two')
        assert harness.calls[-1][0] == 'wake-two' and harness.calls[-1][2]['binding_id'] == 'binding-two'
        report = await second.execute('report', {'items': [{'ref': '#1', 'status': 'handled'}]}, task_id='task-two')
        assert report.error == 'not_reportable'
        assert not [call for call in harness.calls if call[1] == 'report']
    asyncio.run(run())


def test_reference_mapping_restores_after_real_store_adds_namespace(tmp_path):
    from agent_app.application.repositories.session_store import SessionStore
    from icarus.core.identity import SessionIdentity
    from apps.agent.test.agent_app.application.test_scoped_execution import request_v2, TASK, EXECUTION, SESSION, WAKE
    async def run():
        store = SessionStore(tmp_path/'data')
        await store.start()
        identity = SessionIdentity.create(tmp_path/'workspace', SESSION)
        await store.reserve_scoped(request_v2(), identity=identity, execution_id=EXECUTION, task_id=TASK)
        await store.update_scoped(WAKE, {'binding': {'binding_id': 'binding'}})
        harness = Harness()
        async def append(**values): return await store.append_internal(identity, **values)
        async def load(update_types): return await store.read_internal(identity, update_types=set(update_types))
        try:
            for _ in range(2):
                plugin = harness.plugin(task=TASK, wake=WAKE)
                plugin.append_record, plugin.load_records = append, load
                await plugin.on_input(plugin.input_source, UserInputEvent(task_id=TASK, prompt='work'))
                assert plugin.references.resolve('#1')['observation_id'] == ONE
            rows = await load({'argus_reference'})
            assert len(rows) == 1 and rows[0].payload['wake_id'] == WAKE
        finally: await store.close()
    asyncio.run(run())


def test_finished_task_and_wrong_tool_name_cannot_confirm_late_body():
    from icarus.core.contracts.input import InputFinishedEvent
    async def run():
        harness = Harness()
        plugin = harness.plugin()
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        result = await plugin.execute('wait', {}, task_id='task', tool_call_id='late')
        await complete(plugin, result, 'late', operation='history')
        assert not [call for call in harness.calls if call[1] == 'deliver']
        await plugin.on_finished(plugin.input_source, InputFinishedEvent(task_id='task', status='cancelled'))
        await complete(plugin, result, 'late', operation='wait')
        assert not [call for call in harness.calls if call[1] == 'deliver']
    asyncio.run(run())


@pytest.mark.parametrize('history_proof', ['confirmed', 'clipped', 'failed'])
def test_long_history_page_advances_only_after_confirmed_page_and_contiguous_segmented_read(history_proof):
    async def run():
        harness = Harness()
        harness.read_text = 'x' * 20001
        original = harness.call
        older_id = '60000000-0000-4000-8000-000000000003'
        history_item = item(TWO, harness.read_text[:20000])
        history_item.update(role='context', origin='history', truncated=True)
        older = item(older_id, 'older historical message')
        older.update(role='context', origin='history')
        async def paged_history(wake, operation, payload):
            if operation == 'history':
                harness.calls.append((wake, operation, payload))
                if payload['cursor'] == 'long-page-complete':
                    return {'protocol_version': 2, 'items': [older], 'next_cursor': 'older-page-complete',
                            'has_more': False, 'truncated': False}
                return {'protocol_version': 2, 'items': [history_item], 'next_cursor': 'long-page-complete',
                        'has_more': True, 'truncated': True}
            result = await original(wake, operation, payload)
            if operation == 'read':
                result['item'].update(role='context', origin='history', truncated=True)
            return result
        harness.call = paged_history
        plugin = harness.plugin()
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        await plugin.on_context_ready(plugin.context_source, ready(plugin, harness.events[-1].sections[0].content))
        page = await plugin.execute('history', {}, task_id='task', tool_call_id='history')
        assert len(page.output['items'][0]['text']) == 20000 and page.output['has_more']
        if history_proof == 'confirmed':
            await complete(plugin, page, 'history', operation='history')
        else:
            proof = ToolExecutionResult(history_proof != 'failed', output={'preview': 'clipped history'})
            await complete(plugin, proof, 'history', operation='history')
        assert plugin.history_cursor is None
        ref = page.output['items'][0]['ref']
        tail = await plugin.execute('read', {'ref': ref, 'text_offset': 20000}, task_id='task', tool_call_id='tail')
        assert tail.output['text'] == 'x' and tail.output['next_text_offset'] is None
        await complete(plugin, tail, 'tail')
        assert plugin.history_cursor is None, 'A terminal tail with a hole cannot advance history'
        head = await plugin.execute('read', {'ref': ref, 'max_chars': 20000}, task_id='task', tool_call_id='head')
        assert head.output['next_text_offset'] == 20000
        await complete(plugin, ToolExecutionResult(True, output={'preview': 'clipped head'}), 'head')
        assert plugin.history_cursor is None
        await complete(plugin, head, 'head')
        if history_proof != 'confirmed':
            assert plugin.history_cursor is None, 'An unconfirmed history page cannot advance after reads'
            await complete(plugin, page, 'history', operation='history')
        assert plugin.history_cursor == 'long-page-complete'
        next_page = await plugin.execute('history', {}, task_id='task', tool_call_id='older')
        assert next_page.output['items'][0]['text'] == 'older historical message'
        assert [payload['cursor'] for _, operation, payload in harness.calls if operation == 'history'] == [
            None, 'long-page-complete']
        report = await plugin.execute('report', {'items': [{'ref': ref, 'status': 'handled'}]}, task_id='task')
        assert report.error == 'not_reportable'
        assert [payload['observation_ids'] for _, operation, payload in harness.calls if operation == 'deliver'] == [[ONE]]
    asyncio.run(run())


def test_history_cursor_waits_for_every_incomplete_item_on_confirmed_page():
    async def run():
        harness = Harness()
        plugin = harness.plugin()
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        harness.items = [item(TWO, 'ab'), item('60000000-0000-4000-8000-000000000003', 'cd')]
        for value in harness.items:
            value.update(role='context', origin='history', truncated=True)
        original = harness.call
        async def context_read(wake, operation, payload):
            result = await original(wake, operation, payload)
            if operation == 'read':
                result['item'].update(role='context', origin='history')
            return result
        harness.call = context_read
        page = await plugin.execute('history', {}, task_id='task', tool_call_id='history')
        await complete(plugin, page, 'history', operation='history')
        first = await plugin.execute('read', {'ref': page.output['items'][0]['ref']}, task_id='task', tool_call_id='first')
        await complete(plugin, first, 'first')
        assert plugin.history_cursor is None, 'Completing one page item cannot skip another incomplete item'
        second = await plugin.execute('read', {'ref': page.output['items'][1]['ref']}, task_id='task', tool_call_id='second')
        await complete(plugin, second, 'second')
        assert plugin.history_cursor == 'next'
        assert not [call for call in harness.calls if call[1] == 'deliver']
    asyncio.run(run())


def test_final_model_view_equality_and_contiguous_read_coverage_gate_whole_item_ack():
    async def run():
        harness = Harness(items=[item(text='ab')])
        harness.items[0]['truncated'] = True
        plugin = harness.plugin()
        await plugin.on_input(plugin.input_source, UserInputEvent(task_id='task', prompt='work'))
        content = harness.events[-1].sections[0].content
        await plugin.on_context_ready(plugin.context_source, ready(plugin, content))
        assert not harness.calls
        first = await plugin.execute('read', {'ref': '#1', 'max_chars': 4}, task_id='task', tool_call_id='first')
        await complete(plugin, ToolExecutionResult(True, output={'preview': 'abcd'}), 'first')
        assert not [p for _, op, p in harness.calls if op == 'deliver']
        tail = await plugin.execute('read', {'ref': '#1', 'text_offset': 4}, task_id='task', tool_call_id='tail')
        assert ONE not in str(tail.output)
        await complete(plugin, tail, 'tail')
        assert not [p for _, op, p in harness.calls if op == 'deliver'], 'Terminal tail is not contiguous coverage'
        await complete(plugin, first, 'first')
        assert [p['observation_ids'] for _, op, p in harness.calls if op == 'deliver'] == [[ONE]]
        assert (await plugin.execute('report', {'items': [{'ref': '#1', 'status': 'no_action'}]}, task_id='task')).success
    asyncio.run(run())
