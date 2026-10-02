import json

import pytest
from playwright.sync_api import expect


def gateway(page, *, reject=False, disconnect=False, records=None):
    state = {'calls': [], 'records': records or [], 'sockets': [], 'disconnect': disconnect}

    def route(socket):
        state['sockets'].append(socket)

        def receive(raw):
            call = json.loads(raw)
            state['calls'].append(call)
            method, params = call['method'], call['params']
            result = {}
            if method == 'session.list':
                result = {'sessions': [{'session_id': 'session-1', 'first_user_input': '测试会话'}]}
            elif method in ('session.get', 'session.create'):
                result = {'workspace_key': 'workspace-key', 'session_id': params.get('session_id', 'session-1'), 'active_task_ids': []}
            elif method == 'session.get_history':
                remaining = [item for item in state['records'] if item['sequence'] > params['after_sequence']]
                batch = remaining[:2]
                result = {'records': batch, 'next_after_sequence': batch[-1]['sequence'] if batch else params['after_sequence'], 'has_more': len(remaining) > 2, 'history_cursor': len(state['records'])}
            elif method == 'session.submit':
                if reject:
                    socket.send(json.dumps({'jsonrpc': '2.0', 'id': call['id'], 'error': {'code': -32000, 'message': '工作区不可用'}}))
                    return
                result = {'task_id': 'task-1'}
                if state['disconnect']:
                    state['disconnect'] = False
                    socket.close()
                    return
            elif method == 'session.cancel':
                emit('task.finished', {'status': 'cancelled'}, socket)
            socket.send(json.dumps({'jsonrpc': '2.0', 'id': call['id'], 'result': result}))

        socket.on_message(receive)

    def emit(kind, payload, socket=None, sequence=None):
        record = {'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1', 'type': kind, 'payload': payload, 'sequence': sequence or len(state['records']) + 1}
        state['records'].append(record)
        (socket or state['sockets'][-1]).send(json.dumps({'jsonrpc': '2.0', 'method': 'runtime.update', 'params': record}))

    page.route_web_socket('**/rpc', route)
    page.reload()
    page.goto(page.url.split('#')[0] + '#/chat?workspace=%2Ftmp%2Fwebui-test&session=session-1')
    expect(page.get_by_text('已连接', exact=True)).to_be_visible()
    return state, emit


def test_stream_tools_cancel_and_history_reload(page):
    state, emit = gateway(page)
    page.get_by_role('textbox', name='发送消息').fill('帮我读取资料')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.get_by_role('button', name='停止执行')).to_be_visible()
    emit('user.message', {'text': '帮我读取资料'})
    emit('assistant.text_delta', {'step': 1, 'text': '正在'})
    emit('assistant.text_delta', {'step': 1, 'text': '读取'})
    expect(page.locator('.chat-assistant')).to_contain_text('正在读取')
    emit('assistant.message', {'step': 1, 'text': '读取完毕'})
    emit('assistant.text_delta', {'step': 1, 'text': '迟到片段'})
    emit('tool.started', {'call_id': 'call-1', 'tool_name': 'read', 'arguments': {'path': 'demo'}})
    emit('tool.completed', {'call_id': 'call-1', 'tool_name': 'read', 'success': True, 'output_preview': {'content': '资料内容'}})
    emit('future.event', {'text': '不可显示'})
    expect(page.locator('.chat-tool summary')).to_have_text('read · 已完成')
    page.locator('.chat-tool summary').click()
    expect(page.locator('.chat-tool pre')).to_contain_text('资料内容')
    expect(page.locator('.chat-assistant')).to_contain_text('读取完毕')
    assert page.locator('.chat-assistant').count() == 1
    page.get_by_role('button', name='停止执行').click()
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0)
    page.reload()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible()
    expect(page.locator('.chat-assistant')).to_contain_text('读取完毕')
    assert page.locator('.chat-assistant').count() == 1
    assert any(call['method'] == 'session.subscribe' for call in state['calls'])
    assert next(call for call in state['calls'] if call['method'] == 'session.cancel')['params']['task_id'] == 'task-1'


def test_rpc_error_preserves_editable_draft(page):
    gateway(page, reject=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('保留输入')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('工作区不可用')
    expect(editor).to_have_value('保留输入')
    expect(editor).to_be_enabled()


def test_disconnect_does_not_resubmit_and_manual_retry_reuses_id(page):
    state, _ = gateway(page, disconnect=True)
    page.get_by_role('textbox', name='发送消息').fill('只执行一次')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.get_by_role('button', name='重试发送')).to_be_visible()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=10000)
    calls = [call for call in state['calls'] if call['method'] == 'session.submit']
    assert len(calls) == 1
    page.get_by_role('button', name='重试发送').click()
    expect(page.get_by_role('textbox', name='发送消息')).to_have_value('')
    calls = [call for call in state['calls'] if call['method'] == 'session.submit']
    assert len(calls) == 2
    assert calls[0]['params']['submission_id'] == calls[1]['params']['submission_id']


def test_new_session_and_switching_clears_previous_transcript(page):
    state, emit = gateway(page)
    emit('assistant.message', {'step': 1, 'text': '旧会话内容'})
    expect(page.locator('.chat-assistant')).to_contain_text('旧会话内容')
    state['records'].clear()
    page.get_by_role('button', name='新建会话').click()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible()
    expect(page.locator('.chat-assistant')).to_have_count(0)
    assert any(call['method'] == 'session.create' for call in state['calls'])


def test_paginated_history_and_duplicate_notifications(page):
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
                'type': kind, 'payload': payload, 'sequence': index + 1}
               for index, (kind, payload) in enumerate([
                   ('user.message', {'text': '旧问题'}),
                   ('assistant.thinking', {'text': '已思考', 'step': 1}),
                   ('assistant.message', {'text': '旧答案', 'step': 1}),
                   ('task.finished', {'status': 'completed'}),
               ])]
    state, emit = gateway(page, records=records)
    expect(page.locator('.chat-assistant')).to_contain_text('旧答案')
    assert len([call for call in state['calls'] if call['method'] == 'session.get_history']) == 2
    emit('user.message', {'text': '旧问题'}, sequence=1)
    expect(page.locator('.chat-user')).to_have_count(1)
    emit('task.started', {})
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0)


@pytest.mark.parametrize('width', [390, 768])
def test_chat_narrow_layout(page, width):
    page.set_viewport_size({'width': width, 'height': 844})
    gateway(page)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('.shell-main').evaluate('el => el.scrollWidth <= el.clientWidth')


def test_malformed_protocol_response_reconnects(page):
    state, _ = gateway(page)
    state['sockets'][-1].send('{broken-json')
    expect(page.get_by_role('alert')).to_contain_text('连接已断开')
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=10000)
    assert len(state['sockets']) >= 2


@pytest.mark.parametrize('width', [390, 1440])
def test_only_messages_scroll_and_reading_history_does_not_jump(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    _, emit = gateway(page)
    for step in range(1, 19):
        emit('assistant.message', {'step': step, 'text': ('足够长的历史内容。' * 20)})
    messages = page.locator('.chat-transcript')
    expect(page.locator('.chat-assistant')).to_have_count(18)
    page.wait_for_function("document.querySelector('.chat-transcript').scrollTop > 0")
    composer = page.locator('.chat-compose').bounding_box()
    assert composer['y'] + composer['height'] <= 900
    assert page.locator('.shell-main').evaluate('el => el.scrollHeight <= el.clientHeight + 1')
    messages.evaluate('el => el.scrollTop = 0')
    expect(page.get_by_role('button', name='回到最新消息')).to_be_visible()
    emit('assistant.message', {'step': 19, 'text': '新的输出'})
    expect(page.locator('.chat-assistant').last).to_have_text('Icarus新的输出')
    assert messages.evaluate('el => el.scrollTop') == 0
    assert page.locator('.chat-compose').bounding_box() == composer
    page.get_by_role('button', name='回到最新消息').click()
    page.wait_for_function("document.querySelector('.chat-transcript').scrollTop > 0")


def test_keyboard_cannot_submit_second_task_while_running(page):
    state, emit = gateway(page)
    emit('task.started', {})
    expect(page.get_by_role('button', name='停止执行')).to_be_visible()
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('下一条消息')
    editor.press('Control+Enter')
    assert not any(call['method'] == 'session.submit' for call in state['calls'])
    expect(editor).to_have_value('下一条消息')
