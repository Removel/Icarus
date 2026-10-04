import json

import pytest
from playwright.sync_api import expect


def gateway(page, *, reject=False, disconnect=False, records=None, sessions=None, selected='session-1', generated_title=None, hold_title=False, hold_submit=False, steer_status='accepted', hold_steer=False, disconnect_steer=False):
    state = {'calls': [], 'records': records or [], 'sockets': [], 'disconnect': disconnect,
             'sessions': sessions or [{'session_id': 'session-1', 'first_user_input': '测试会话'}]}

    def route(socket):
        state['sockets'].append(socket)

        def receive(raw):
            call = json.loads(raw)
            state['calls'].append(call)
            method, params = call['method'], call['params']
            result = {}
            if method == 'session.list':
                result = {'sessions': state['sessions']}
            elif method in ('session.get', 'session.create'):
                result = {'workspace_key': 'workspace-key', 'session_id': params.get('session_id', 'session-1'), 'active_task_ids': []}
                if method == 'session.create':
                    state['sessions'].insert(0, {'session_id': result['session_id'], 'first_user_input': ''})
            elif method == 'session.get_history':
                remaining = [item for item in state['records'] if item['sequence'] > params['after_sequence']]
                batch = remaining[:2]
                result = {'records': batch, 'next_after_sequence': batch[-1]['sequence'] if batch else params['after_sequence'], 'has_more': len(remaining) > 2, 'history_cursor': len(state['records'])}
            elif method == 'session.generate_title':
                if hold_title:
                    state['pending_title'] = (socket, call['id'])
                    return
                result = {'title': generated_title}
                if generated_title:
                    for row in state['sessions']:
                        if row['session_id'] == params['session_id']:
                            row['title'] = generated_title
            elif method == 'session.delete':
                state['sessions'] = [item for item in state['sessions'] if item['session_id'] != params['session_id']]
                result = {'status': 'discarded'}
            elif method == 'session.submit':
                if hold_submit:
                    state['pending_submit'] = (socket, call['id'])
                    return
                if reject:
                    socket.send(json.dumps({'jsonrpc': '2.0', 'id': call['id'], 'error': {'code': -32000, 'message': '工作区不可用'}}))
                    return
                result = {'task_id': 'task-' + str(sum(item['method'] == 'session.submit' for item in state['calls']))}
                if state['disconnect']:
                    state['disconnect'] = False
                    socket.close()
                    return
            elif method == 'session.steer':
                if hold_steer:
                    state['pending_steer'] = (socket, call['id'])
                    return
                if disconnect_steer and not state.get('steer_disconnected'):
                    state['steer_disconnected'] = True
                    socket.close()
                    return
                result = {'task_id': params['task_id'], 'status': steer_status}
            elif method == 'session.cancel':
                emit('task.finished', {'status': 'cancelled'}, socket, task_id=params['task_id'])
            socket.send(json.dumps({'jsonrpc': '2.0', 'id': call['id'], 'result': result}))

        socket.on_message(receive)

    def emit(kind, payload, socket=None, sequence=None, task_id='task-1'):
        record = {'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': task_id, 'type': kind, 'payload': payload, 'sequence': sequence or len(state['records']) + 1}
        state['records'].append(record)
        (socket or state['sockets'][-1]).send(json.dumps({'jsonrpc': '2.0', 'method': 'runtime.update', 'params': record}))

    page.route_web_socket('**/rpc', route)
    page.reload()
    page.goto(page.url.split('#')[0] + '#/chat?workspace=%2Ftmp%2Fwebui-test&session=' + selected)
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
    page.locator('.chat-process > summary').click()
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


@pytest.mark.parametrize('event_before_response', [True, False])
def test_pending_send_is_immediate_editable_and_reconciles_once(page, event_before_response):
    state, emit = gateway(page, hold_submit=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('立即显示的问题')
    editor.press('Enter')
    expect(page.locator('.chat-user')).to_have_text('你立即显示的问题')
    expect(page.get_by_text('正在执行', exact=True)).to_be_visible()
    expect(page.get_by_text('正在思考中', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='排队发送', exact=True)).to_be_disabled()
    expect(page.get_by_role('button', name='停止执行', exact=True)).to_be_enabled()
    expect(page.get_by_text('正在发送…', exact=True)).to_have_count(0)
    expect(editor).to_have_value('')
    expect(editor).to_be_enabled()
    editor.fill('正在编辑下一条')
    assert len([call for call in state['calls'] if call['method'] == 'session.submit']) == 1
    socket, request_id = state['pending_submit']
    if event_before_response:
        emit('user.message', {'text': '立即显示的问题'})
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'task_id': 'task-1'}}))
    expect(page.get_by_role('button', name='停止执行', exact=True)).to_be_enabled()
    if not event_before_response:
        emit('user.message', {'text': '立即显示的问题'})
    expect(page.locator('.chat-user')).to_have_count(1)
    expect(editor).to_have_value('正在编辑下一条')
    expect(page.get_by_text('正在思考中', exact=True)).to_be_visible()
    emit('task.finished', {'status': 'completed'})
    expect(page.get_by_text('正在思考中', exact=True)).to_have_count(0)
    editor.fill('')
    page.reload()
    expect(page.locator('.chat-user')).to_have_count(1)


def test_pending_send_rejection_restores_both_submitted_message_and_new_draft(page):
    state, _ = gateway(page, hold_submit=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('需要重试的问题')
    editor.press('Enter')
    expect(page.locator('.chat-user')).to_have_count(1)
    editor.fill('新草稿')
    socket, request_id = state['pending_submit']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32000, 'message': '工作区不可用'}}))
    expect(page.get_by_role('alert')).to_contain_text('工作区不可用')
    expect(editor).to_have_value('需要重试的问题\n新草稿')
    expect(editor).to_be_enabled()
    expect(page.locator('.chat-user')).to_have_count(0)
    expect(page.get_by_text('正在思考中', exact=True)).to_have_count(0)


@pytest.mark.parametrize('kind,payload', [
    ('assistant.thinking_delta', {'step': 1, 'text': '正在分析'}),
    ('assistant.text_delta', {'step': 1, 'text': '首个输出片段'}),
    ('tool.started', {'step': 1, 'call_id': 'first-tool', 'tool_name': 'read', 'arguments': {}}),
])
def test_thinking_placeholder_is_immediate_and_replaced_by_first_response(page, kind, payload):
    state, emit = gateway(page, hold_submit=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('等待首个响应')
    editor.press('Enter')
    placeholder = page.get_by_text('正在思考中', exact=True)
    expect(placeholder).to_be_visible()
    expect(editor).to_be_enabled()
    socket, request_id = state['pending_submit']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'task_id': 'task-1'}}))
    expect(placeholder).to_be_visible()
    emit(kind, payload)
    expect(placeholder).to_have_count(0)
    if kind == 'assistant.text_delta':
        expect(page.locator('.chat-assistant')).to_contain_text('首个输出片段')
    else:
        expect(page.locator('.chat-process > summary')).to_contain_text('思考与工具')


def test_stop_during_submit_cancels_when_task_is_accepted(page):
    state, _ = gateway(page, hold_submit=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('准备阶段就停止')
    editor.press('Enter')
    stop = page.get_by_role('button', name='停止执行', exact=True)
    expect(stop).to_be_enabled()
    stop.click()
    assert not any(call['method'] == 'session.cancel' for call in state['calls'])
    socket, request_id = state['pending_submit']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'task_id': 'task-1'}}))
    expect(stop).to_have_count(0)
    cancel = [call for call in state['calls'] if call['method'] == 'session.cancel']
    assert len(cancel) == 1
    assert cancel[0]['params']['task_id'] == 'task-1'
    expect(editor).to_be_enabled()
    expect(editor).to_have_value('')


def test_first_prompt_title_updates_before_acceptance_and_reverts_on_rejection(page):
    state, _ = gateway(page, hold_submit=True, sessions=[{'session_id': 'session-1', 'first_user_input': ''}])
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('即时会话标题')
    editor.press('Enter')
    expect(page.locator('.chat-conversation-heading h2')).to_have_text('即时会话标题')
    expect(page.locator('.chat-session')).to_have_text('即时会话标题')
    socket, request_id = state['pending_submit']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32000, 'message': '暂时不可用'}}))
    expect(page.get_by_role('alert')).to_contain_text('暂时不可用')
    expect(page.locator('.chat-conversation-heading h2')).to_have_text('新会话')
    expect(editor).to_have_value('即时会话标题')


def test_burst_output_and_thinking_reveal_progressively_and_history_is_immediate(page):
    page.emulate_media(reduced_motion='no-preference')
    _, emit = gateway(page)
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    # Hold presentation frames so assertions do not depend on machine speed.
    page.evaluate('''() => {
        window.presentationFrames = new Map();
        window.presentationId = 0;
        window.presentationTime = performance.now();
        window.requestAnimationFrame = callback => { const id = ++window.presentationId; window.presentationFrames.set(id, callback); return id; };
        window.cancelAnimationFrame = id => window.presentationFrames.delete(id);
        window.advancePresentation = () => {
            window.presentationTime += 16;
            const frames = Array.from(window.presentationFrames.values());
            window.presentationFrames.clear();
            frames.forEach(callback => callback(window.presentationTime));
        };
    }''')
    thinking = '思考中的内容，应该渐进显示而不是整块突然出现。' * 20
    answer = '平滑输出的正文，包含完整结果和后续补全的文字。' * 30 + '🌷'
    emit('assistant.thinking_delta', {'step': 1, 'text': thinking})
    emit('assistant.text_delta', {'step': 1, 'text': answer[:200]})
    emit('assistant.message', {'step': 1, 'text': answer})
    emit('assistant.thinking', {'step': 1, 'text': thinking})
    emit('task.finished', {'status': 'completed'})
    content = page.locator('.chat-assistant .chat-markdown')
    thought = page.locator('.chat-thinking pre')
    expect(content).to_have_attribute('data-streaming', 'true')
    expect(thought).to_have_attribute('data-streaming', 'true')
    page.evaluate('window.advancePresentation()')
    page.wait_for_function("document.querySelector('.chat-assistant .chat-markdown').textContent.length > 0", polling=20)
    assert len(content.inner_text()) < len(answer)
    assert 0 < len(thought.text_content()) < len(thinking)
    page.evaluate('''() => { for (let i = 0; i < 80; i++) window.advancePresentation(); }''')
    expect(content).to_have_text(answer)
    expect(thought).to_have_text(thinking)
    expect(content).not_to_have_attribute('data-streaming', 'true')
    emit('assistant.text_delta', {'step': 1, 'text': '迟到片段'})
    expect(content).to_have_text(answer)
    page.reload()
    expect(content).to_have_text(answer)
    expect(thought).to_have_text(thinking)
    expect(content).not_to_have_attribute('data-streaming', 'true')


def test_reduced_motion_displays_full_live_reply_without_replay(page):
    _, emit = gateway(page)
    answer = '遵循减少动效设置。' * 40
    emit('assistant.message', {'step': 1, 'text': answer})
    content = page.locator('.chat-assistant .chat-markdown')
    expect(content).to_have_text(answer)
    expect(content).not_to_have_attribute('data-streaming', 'true')


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
@pytest.mark.parametrize('motion', ['reduce', 'no-preference'])
def test_only_messages_scroll_and_reading_history_does_not_jump(page, width, motion):
    page.emulate_media(reduced_motion=motion)
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


def test_enter_queues_messages_and_dispatches_in_order_after_finish(page):
    state, emit = gateway(page)
    emit('task.started', {}, task_id='task-0')
    expect(page.get_by_role('button', name='停止执行')).to_be_visible()
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('下一条消息')
    editor.press('Enter')
    assert not any(call['method'] == 'session.submit' for call in state['calls'])
    expect(editor).to_have_value('')
    expect(page.locator('.chat-queue-text')).to_have_text('下一条消息')
    editor.fill('第三条消息')
    page.get_by_role('button', name='排队发送', exact=True).click()
    expect(page.locator('.chat-queue li')).to_have_count(2)
    emit('task.finished', {'status': 'completed'}, task_id='task-0')
    expect(page.locator('.chat-user')).to_have_text('你下一条消息')
    expect(page.locator('.chat-queue li')).to_have_count(1)
    assert len([call for call in state['calls'] if call['method'] == 'session.submit']) == 1
    emit('task.finished', {'status': 'completed'})
    expect(page.locator('.chat-user').last).to_have_text('你第三条消息')
    expect(page.locator('.chat-queue')).to_have_count(0)
    submissions = [call['params'] for call in state['calls'] if call['method'] == 'session.submit']
    assert [item['prompt'] for item in submissions] == ['下一条消息', '第三条消息']
    assert len({item['submission_id'] for item in submissions}) == 2
    emit('task.finished', {'status': 'completed'}, task_id='task-2')
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0)


def test_markdown_stream_final_and_reload(page):
    state, emit = gateway(page)
    emit('assistant.text_delta', {'step': 1, 'text': '**流式加粗**'})
    expect(page.locator('.chat-markdown strong')).to_have_text('流式加粗')
    content = '# 标题\n\n**重点**\n\n- 列表项\n\n```python\nprint(1)\n```\n\n| 名称 | 值 |\n| --- | --- |\n| A | B |\n\n[链接](https://example.com)\n\n<script>window.bad = true</script>\n\n[危险](javascript:alert(1))'
    emit('assistant.message', {'step': 1, 'text': content})
    expect(page.locator('.chat-markdown h1')).to_have_text('标题')
    expect(page.locator('.chat-markdown li')).to_have_text('列表项')
    expect(page.locator('.chat-markdown pre code')).to_contain_text('print(1)')
    expect(page.locator('.chat-markdown td')).to_have_text(['A', 'B'])
    assert page.locator('.chat-markdown script').count() == 0
    assert not page.evaluate('Boolean(window.bad)')
    assert page.get_by_role('link', name='危险', exact=True).get_attribute('href') == ''
    page.reload()
    expect(page.locator('.chat-markdown h1')).to_have_text('标题')


def test_session_selection_keeps_scroll_and_deep_link_is_visible(page):
    sessions = [{'session_id': f'session-{i}', 'first_user_input': f'会话 {i}'} for i in range(50)]
    gateway(page, sessions=sessions, selected='session-40')
    listing = page.locator('.chat-session-list')
    selected = listing.locator('[aria-current=page]')
    expect(selected).to_be_in_viewport()
    assert listing.evaluate('el => el.scrollTop') > 0
    next_session = page.get_by_role('button', name='会话 39', exact=True)
    next_session.scroll_into_view_if_needed()
    position = listing.evaluate('el => el.scrollTop')
    next_session.click()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible()
    expect(next_session).to_have_attribute('aria-current', 'page')
    assert abs(listing.evaluate('el => el.scrollTop') - position) < 2


def test_created_session_is_listed_and_first_prompt_updates_title(page):
    state, _ = gateway(page)
    page.get_by_role('button', name='新建会话').click()
    page.wait_for_function("new URLSearchParams(location.hash.split('?')[1]).get('session') !== 'session-1'")
    expect(page.get_by_text('已连接', exact=True)).to_be_visible()
    selected = page.locator('.chat-session-list [aria-current=page]')
    expect(selected).to_contain_text('新会话')
    page.get_by_role('textbox', name='发送消息').fill('新会话的首条问题')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(selected).to_contain_text('新会话的首条问题')
    expect(page.locator('.chat-conversation-heading h2')).to_have_text('新会话的首条问题')


def test_enter_shift_enter_and_ime_composition(page):
    state, _ = gateway(page)
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_enabled()
    editor.fill('第一行')
    editor.press('Shift+Enter')
    editor.press('a')
    expect(editor).to_have_value('第一行\na')
    editor.dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
    assert not any(call['method'] == 'session.submit' for call in state['calls'])
    editor.press('Enter')
    expect(editor).to_have_value('')
    assert len([call for call in state['calls'] if call['method'] == 'session.submit']) == 1


def test_switch_and_creation_reuse_socket_and_do_not_reload_list(page):
    state, _ = gateway(page, sessions=[{'session_id': 'session-1', 'first_user_input': '第一段'}, {'session_id': 'session-2', 'first_user_input': '第二段'}])
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    sockets = len(state['sockets'])
    page.get_by_role('button', name='第二段', exact=True).click()
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    assert len(state['sockets']) == sockets
    page.get_by_role('button', name='新建会话').click()
    page.wait_for_function("new URLSearchParams(location.hash.split('?')[1]).get('session') !== 'session-2'")
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    assert len(state['sockets']) == sockets
    assert len([call for call in state['calls'] if call['method'] == 'session.list']) == 1
    assert len([call for call in state['calls'] if call['method'] == 'session.get_history']) == 2
    assert any(call['method'] == 'session.unsubscribe' for call in state['calls'])


@pytest.mark.parametrize('width', [390, 1440])
def test_delete_current_conversation_updates_list_and_survives_reload(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    state, _ = gateway(page, sessions=[{'session_id': 'session-1', 'first_user_input': '第一段'}, {'session_id': 'session-2', 'first_user_input': '第二段'}])
    page.get_by_role('button', name='第一段', exact=True).hover()
    page.get_by_role('button', name='删除会话：第一段').click()
    dialog = page.get_by_role('dialog', name='删除这段对话？', exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.locator('.semi-modal-body')).not_to_be_visible()
    assert dialog.bounding_box()['height'] < 160
    page.screenshot(path=f'test-results/chat-delete-simple-{width}.png')
    dialog.get_by_role('button', name='删除', exact=True).click()
    expect(dialog).to_have_count(0)
    expect(page.get_by_role('button', name='第一段', exact=True)).to_have_count(0)
    page.wait_for_function("new URLSearchParams(location.hash.split('?')[1]).get('session') === 'session-2'")
    page.reload()
    expect(page.get_by_role('button', name='第二段', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='第一段', exact=True)).to_have_count(0)
    assert any(call['method'] == 'session.delete' for call in state['calls'])


def test_process_is_grouped_and_message_navigation_preserves_reading_position(page):
    _, emit = gateway(page)
    for step in range(1, 5):
        emit('assistant.thinking', {'step': step, 'text': '详细思考' * 100})
        emit('tool.completed', {'call_id': str(step), 'tool_name': 'read', 'success': True, 'output_preview': 'result'})
        emit('assistant.message', {'step': step, 'text': f'回答 {step}。' * 120})
    expect(page.locator('.chat-process')).to_have_count(1)
    assert page.locator('.chat-process').get_attribute('open') is None
    expect(page.locator('.chat-thinking pre').first).not_to_be_visible()
    page.get_by_role('button', name='定位消息', exact=True).click()
    page.get_by_placeholder('搜索本段对话…').fill('回答 1')
    page.get_by_role('navigation', name='消息定位').get_by_role('button').click()
    first = page.locator('.chat-assistant').first
    expect(first).to_be_in_viewport()
    emit('assistant.message', {'step': 5, 'text': '新的回答'})
    expect(first).to_be_in_viewport()
    page.get_by_role('button', name='下一条消息', exact=True).click()
    expect(page.locator('.chat-assistant').nth(1)).to_be_in_viewport()
    page.get_by_role('button', name='回到最新消息', exact=True).click()
    page.screenshot(path='test-results/refinement-chat.png', full_page=True)


def test_draft_confirmation_happens_before_creating_a_session(page):
    state, _ = gateway(page)
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_enabled()
    editor.fill('未发送的草稿')
    page.get_by_role('button', name='新建会话').click()
    dialog = page.get_by_role('dialog', name='放弃未发送的消息？')
    expect(dialog).to_be_visible()
    assert not any(call['method'] == 'session.create' for call in state['calls'])
    dialog.get_by_role('button', name='放弃', exact=True).click()
    expect(dialog).to_have_count(0)
    page.wait_for_function("new URLSearchParams(location.hash.split('?')[1]).get('session') !== 'session-1'")
    expect(editor).to_be_enabled()
    assert len([call for call in state['calls'] if call['method'] == 'session.create']) == 1


@pytest.mark.parametrize('width', [390, 1440])
def test_discard_draft_on_module_change_really_clears_it(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    gateway(page)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('未发送的草稿')
    page.get_by_role('link', name='记忆', exact=True).click()
    dialog = page.get_by_role('dialog', name='放弃未发送的消息？')
    expect(dialog).to_be_visible()
    expect(dialog.locator('.semi-modal-body')).not_to_be_visible()
    assert dialog.bounding_box()['height'] < 160
    page.screenshot(path=f'test-results/chat-discard-simple-{width}.png')
    dialog.get_by_role('button', name='继续编辑').click()
    expect(editor).to_have_value('未发送的草稿')
    page.get_by_role('link', name='记忆', exact=True).click()
    dialog.get_by_role('button', name='放弃', exact=True).click()
    expect(page.get_by_role('heading', name='记忆', exact=True)).to_be_visible()
    page.get_by_role('link', name='对话', exact=True).click()
    expect(editor).to_have_value('')
    page.get_by_role('link', name='记忆', exact=True).click()
    expect(dialog).to_have_count(0)
    expect(page.get_by_role('heading', name='记忆', exact=True)).to_be_visible()


@pytest.mark.parametrize('motion', ['reduce', 'no-preference'])
def test_process_wave_continues_after_response_and_stops_on_finish(page, motion):
    page.emulate_media(reduced_motion=motion)
    _, emit = gateway(page)
    emit('task.started', {})
    emit('assistant.thinking_delta', {'text': '正在分析', 'step': 1})
    emit('assistant.message', {'text': '阶段性回答', 'step': 1})
    wave = page.locator('.chat-process-status .chat-thinking-wave')
    expect(wave).to_be_visible()
    expect(wave).to_have_attribute('data-active', 'true')
    assert wave.locator('i').count() == 5
    assert wave.locator('i').first.evaluate('el => getComputedStyle(el).animationName') == (
        'none' if motion == 'reduce' else 'chat-thinking-wave')
    emit('task.finished', {'status': 'completed'})
    expect(wave).to_have_count(0)
    expect(page.locator('.chat-process-status')).to_have_text('1 项')


@pytest.mark.parametrize('width', [390, 1440])
def test_process_window_fits_five_rows_and_respects_manual_scroll(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    _, emit = gateway(page)
    emit('task.started', {})
    for step in range(1, 9):
        emit('assistant.thinking', {'step': step, 'text': f'过程 {step}' * 100})
    expect(page.locator('.chat-thinking')).to_have_count(8)
    page.locator('.chat-process > summary').click()
    items = page.locator('.chat-process-items')
    page.wait_for_function('''() => {
        const el = document.querySelector('.chat-process-items');
        return el.scrollTop > 0 && el.scrollHeight - el.scrollTop - el.clientHeight < 2;
    }''')
    assert items.evaluate('el => el.clientHeight') == 194
    visible = items.evaluate('''el => {
        const bounds = el.getBoundingClientRect();
        return [...el.children].filter(row => {
            const b = row.getBoundingClientRect();
            return b.top >= bounds.top - 1 && b.bottom <= bounds.bottom + 1;
        }).length;
    }''')
    assert visible == 5
    items.evaluate('el => el.scrollTop = 0')
    expect(page.get_by_role('button', name='查看最新过程')).to_have_count(0)
    page.wait_for_function("document.querySelector('.chat-process-items').scrollTop === 0")
    emit('assistant.thinking', {'step': 9, 'text': '新增过程'})
    expect(page.locator('.chat-thinking')).to_have_count(9)
    assert items.evaluate('el => el.scrollTop') == 0
    page.locator('.chat-thinking summary').first.click()
    assert items.evaluate('el => el.scrollTop') == 0
    assert items.evaluate('el => el.clientHeight') == 194
    items.evaluate('el => el.scrollTop = el.scrollHeight')
    page.wait_for_function('''() => {
        const el = document.querySelector('.chat-process-items');
        return el.scrollHeight - el.scrollTop - el.clientHeight < 2;
    }''')
    items.focus()
    items.press('End')
    emit('assistant.thinking', {'step': 10, 'text': '最新过程'})
    expect(page.locator('.chat-thinking').last).to_be_in_viewport()
    page.screenshot(path=f'test-results/chat-process-{width}.png', full_page=True)


@pytest.mark.parametrize('width', [390, 1440])
def test_composer_actions_are_inside_input_box_without_keyboard_hint(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    gateway(page)
    editor = page.get_by_role('textbox', name='发送消息')
    empty_height = editor.bounding_box()['height']
    assert empty_height < 60
    editor.fill('消息')
    assert abs(editor.bounding_box()['height'] - empty_height) <= 1
    editor.fill('第一行\n第二行\n第三行')
    page.wait_for_function("document.querySelector('.chat-compose textarea').getBoundingClientRect().height > 60")
    editor.fill('')
    page.wait_for_function("document.querySelector('.chat-compose textarea').getBoundingClientRect().height < 60")
    editor.fill('消息')
    box = page.locator('.chat-input-box').bounding_box()
    send = page.get_by_role('button', name='发送', exact=True).bounding_box()
    assert box['x'] < send['x'] and box['y'] < send['y']
    assert send['x'] + send['width'] < box['x'] + box['width']
    assert send['y'] + send['height'] < box['y'] + box['height']
    assert page.locator('.chat-compose-hint').count() == 0


def test_queue_during_preparation_is_editable_and_stop_pauses_dispatch(page):
    state, emit = gateway(page, hold_submit=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('第一条')
    editor.press('Enter')
    editor.fill('第二条')
    editor.press('Enter')
    editor.fill('第三条')
    editor.press('Enter')
    expect(page.locator('.chat-queue li')).to_have_count(2)
    page.get_by_role('button', name='编辑待发送消息 1', exact=True).click()
    expect(editor).to_have_value('第二条')
    expect(page.locator('.chat-queue-text')).to_have_text('第三条')
    page.get_by_role('button', name='停止执行').click()
    socket, request_id = state['pending_submit']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'task_id': 'task-1'}}))
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0)
    expect(page.get_by_text('待发送 · 1 · 已暂停', exact=True)).to_be_visible()
    assert len([call for call in state['calls'] if call['method'] == 'session.submit']) == 1
    page.get_by_role('button', name='继续队列').click()
    expect(page.locator('.chat-user').last).to_have_text('你第三条')
    expect(editor).to_have_value('第二条')
    page.get_by_role('button', name='停止执行').click()
    socket, request_id = state['pending_submit']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'task_id': 'task-2'}}))
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0)


def test_queue_is_protected_on_navigation_and_can_be_discarded(page):
    state, emit = gateway(page)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('不要丢失的排队消息')
    editor.press('Enter')
    page.get_by_role('link', name='记忆', exact=True).click()
    dialog = page.get_by_role('dialog', name='放弃未发送的消息？')
    expect(dialog).to_be_visible()
    expect(dialog.locator('.semi-modal-body')).not_to_be_visible()
    dialog.get_by_role('button', name='继续编辑').click()
    expect(page.locator('.chat-queue li')).to_have_count(1)
    page.get_by_role('link', name='记忆', exact=True).click()
    expect(dialog).to_be_visible()
    emit('task.finished', {'status': 'completed'})
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0)
    expect(page.locator('.chat-queue li')).to_have_count(1)
    assert not any(call['method'] == 'session.submit' for call in state['calls'])
    dialog.get_by_role('button', name='放弃', exact=True).click()
    page.get_by_role('link', name='对话', exact=True).click()
    expect(editor).to_have_value('')
    expect(page.locator('.chat-queue')).to_have_count(0)
    emit('task.finished', {'status': 'completed'})
    assert not any(call['method'] == 'session.submit' for call in state['calls'])


def test_queue_failure_restores_message_and_pauses_remaining_queue(page):
    state, emit = gateway(page, reject=True)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    for text in ['需要恢复', '暂缓发送']:
        editor.fill(text)
        editor.press('Enter')
    editor.fill('继续编辑的草稿')
    emit('task.finished', {'status': 'completed'})
    expect(page.get_by_role('alert')).to_contain_text('工作区不可用')
    expect(editor).to_have_value('需要恢复\n继续编辑的草稿')
    expect(page.get_by_text('待发送 · 1 · 已暂停', exact=True)).to_be_visible()
    assert len([call for call in state['calls'] if call['method'] == 'session.submit']) == 1
    page.get_by_role('button', name='删除待发送消息 1', exact=True).click()
    expect(page.locator('.chat-queue')).to_have_count(0)


@pytest.mark.parametrize('event_before_response', [True, False])
def test_steer_uses_active_task_and_applied_event_reconciles_once(page, event_before_response):
    state, emit = gateway(page, hold_steer=True)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('换一个方向')
    page.get_by_role('button', name='引导当前任务', exact=True).click()
    expect(editor).to_have_value('')
    expect(page.locator('.chat-user')).to_contain_text('换一个方向')
    editor.fill('新的草稿')
    socket, request_id = state['pending_steer']
    if event_before_response:
        emit('user.correction', {'text': '换一个方向'})
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'task_id': 'task-1', 'status': 'accepted'}}))
    expect(page.get_by_role('button', name='引导当前任务')).to_be_enabled()
    if not event_before_response:
        emit('user.correction', {'text': '换一个方向'})
    expect(page.locator('.chat-user')).to_have_count(1)
    expect(page.locator('.chat-message-state')).to_have_text('引导已应用')
    expect(editor).to_have_value('新的草稿')
    call = next(call for call in state['calls'] if call['method'] == 'session.steer')
    assert call['params']['task_id'] == 'task-1'
    assert call['params']['prompt'] == '换一个方向'
    assert call['params']['submission_id']
    assert not any(call['method'] == 'session.submit' for call in state['calls'])


@pytest.mark.parametrize('status', ['not_running', 'already_cancelling'])
def test_rejected_steer_restores_draft_without_creating_another_task(page, status):
    state, emit = gateway(page, steer_status=status)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('迟到的引导')
    page.get_by_role('button', name='引导当前任务').click()
    expect(editor).to_have_value('迟到的引导')
    expect(page.get_by_role('alert')).to_contain_text('消息已恢复')
    expect(page.locator('.chat-user')).to_have_count(0)
    assert not any(call['method'] == 'session.submit' for call in state['calls'])


def test_steer_disconnection_never_replays_automatically_and_retry_reuses_id(page):
    state, emit = gateway(page, disconnect_steer=True)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('引导结果未知')
    page.get_by_role('button', name='引导当前任务').click()
    expect(page.get_by_role('button', name='重试发送', exact=True)).to_be_visible()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=15000)
    calls = [call for call in state['calls'] if call['method'] == 'session.steer']
    assert len(calls) == 1
    page.get_by_role('button', name='重试发送', exact=True).click()
    expect(editor).to_have_value('')
    calls = [call for call in state['calls'] if call['method'] == 'session.steer']
    assert len(calls) == 2
    assert calls[0]['params'] == calls[1]['params']
    assert not any(call['method'] == 'session.submit' for call in state['calls'])


def test_unapplied_steer_is_marked_when_task_stops(page):
    _, emit = gateway(page)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('尚未消费的引导')
    page.get_by_role('button', name='引导当前任务').click()
    expect(page.locator('.chat-message-state')).to_have_text('引导待应用')
    emit('task.finished', {'status': 'cancelled'})
    expect(page.locator('.chat-message-state')).to_have_text('引导未应用')


def test_stop_works_while_steer_confirmation_is_pending(page):
    state, emit = gateway(page, hold_steer=True)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('进行中的引导')
    page.get_by_role('button', name='引导当前任务').click()
    expect(page.locator('.chat-message-state')).to_have_text('引导待应用')
    page.get_by_role('button', name='停止执行').click()
    expect(page.locator('.chat-message-state')).to_have_text('引导未应用')
    socket, request_id = state['pending_steer']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'task_id': 'task-1', 'status': 'accepted'}}))
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0)
    assert any(call['method'] == 'session.cancel' for call in state['calls'])

def test_ai_title_is_background_and_full_title_time_are_available_on_hover(page):
    from datetime import UTC, datetime, timedelta
    page.emulate_media(reduced_motion='no-preference')
    date = (datetime.now(UTC) - timedelta(days=3, hours=1)).isoformat()
    title = '根据服务端首条消息生成的完整会话标题，用于确认截断后仍能查看全文'
    state, _ = gateway(page, hold_title=True, sessions=[{
        'session_id': 'session-1', 'first_user_input': '', 'created_at': date, 'updated_at': date
    }])
    row = page.locator('.chat-session')
    expect(row.locator('small')).to_have_count(0)
    expect(row.locator('svg')).to_have_count(0)
    page.get_by_role('textbox', name='发送消息').fill('整理项目文档')
    page.get_by_role('textbox', name='发送消息').press('Enter')
    expect(page.get_by_role('button', name='停止执行')).to_be_visible()
    expect(page.get_by_role('textbox', name='发送消息')).to_have_value('')
    expect(page.locator('.chat-conversation-heading h2')).to_have_text('整理项目文档')
    assert len([call for call in state['calls'] if call['method'] == 'session.generate_title']) == 1
    socket, request_id = state['pending_title']
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {'title': title}}))
    expect(page.locator('.chat-conversation-heading h2')).to_have_text(title)
    assert page.locator('.chat-conversation-heading .chat-title-change').evaluate('el => getComputedStyle(el).animationName') == 'chat-title-appear'
    row.hover()
    expect(page.locator('.chat-session-tooltip')).to_contain_text(title)
    expect(page.locator('.chat-session-tooltip')).to_contain_text('最近对话')
    expect(page.locator('.chat-session-tooltip')).to_contain_text('创建于')
    page.get_by_role('textbox', name='搜索会话…').fill('项目文档')
    expect(row).to_have_count(1)


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_long_session_title_scrolls_on_hover_and_tooltip_is_readable(page, theme):
    page.emulate_media(color_scheme=theme, reduced_motion='no-preference')
    title = '检查长会话标题悬浮时能够完整滚动阅读，以及提示中的时间在浅色和深色主题下保持清晰可见'
    gateway(page, sessions=[{
        'session_id': 'session-1', 'title': title, 'first_user_input': '原始问题',
        'created_at': '2026-10-01T08:00:00Z', 'updated_at': '2026-10-03T08:00:00Z',
    }, {'session_id': 'session-2', 'title': '短标题', 'first_user_input': ''}])
    row = page.get_by_role('button', name=title, exact=True)
    text = row.locator('.chat-session-title-text')
    expect(row.locator('.chat-session-title')).to_have_class('chat-session-title is-overflowing')
    assert text.evaluate('el => getComputedStyle(el).animationName') == 'none'
    row.hover()
    expect(page.locator('.chat-session-tooltip')).to_contain_text(title)
    assert text.evaluate('el => getComputedStyle(el).animationName') == 'chat-title-scroll'
    text.evaluate('el => { const animation = el.getAnimations()[0]; animation.pause(); animation.currentTime = animation.effect.getTiming().duration * .5; }')
    assert text.evaluate('el => new DOMMatrix(getComputedStyle(el).transform).m41') < 0
    tooltip = page.locator('.chat-session-tooltip')
    contrast = tooltip.evaluate('''el => {
        const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
        const background = rgb(getComputedStyle(el.closest('.semi-tooltip-wrapper')).backgroundColor);
        const label = el.querySelector('span');
        const style = getComputedStyle(label);
        const foreground = rgb(style.color).map((v, i) => v * Number(style.opacity) + background[i] * (1 - Number(style.opacity)));
        const luminance = color => color.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
        const values = [luminance(background), luminance(foreground)].sort((a, b) => a - b);
        return (values[1] + .05) / (values[0] + .05);
    }''')
    assert contrast >= 4.5
    page.screenshot(path=f'test-results/chat-hover-{theme}.png', full_page=True)
    page.get_by_role('heading', name='会话', exact=True).hover()
    assert text.evaluate('el => getComputedStyle(el).animationName') == 'none'
    page.emulate_media(reduced_motion='reduce')
    row.hover()
    assert text.evaluate('el => getComputedStyle(el).animationName') == 'none'
    expect(tooltip).to_contain_text(title)
    short = page.get_by_role('button', name='短标题', exact=True)
    short.hover()
    assert short.locator('.chat-session-title-text').evaluate('el => getComputedStyle(el).animationName') == 'none'
