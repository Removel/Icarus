from playwright.sync_api import expect
from test.packages.chat_app.test_chat import gateway


def test_welcome_does_not_create_until_first_send_and_subscribes_before_submit(page):
    state, _ = gateway(page, selected='', sessions=[])
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_enabled()
    page.get_by_role('button', name='新建会话', exact=True).click()
    assert not any(call['method'] == 'session.create' for call in state['calls'])
    editor.fill('欢迎页的首条消息')
    editor.press('Enter')
    expect(page.locator('.chat-user')).to_contain_text('欢迎页的首条消息')
    methods = [call['method'] for call in state['calls']]
    assert methods.count('session.create') == methods.count('session.submit') == 1
    assert methods.index('session.subscribe') < methods.index('session.submit')
    create = next(c['params'] for c in state['calls'] if c['method'] == 'session.create')
    submit = next(c['params'] for c in state['calls'] if c['method'] == 'session.submit')
    assert create['session_id'] == submit['session_id']
    assert create['load_runtime'] is False
    assert page.get_by_role('textbox', name='发送消息').input_value() == ''


def test_duplicate_first_send_waits_for_single_creation(page):
    state, _ = gateway(page, selected='', sessions=[], hold_create=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('只创建一次')
    editor.press('Enter')
    page.wait_for_function("document.querySelector('textarea').disabled")
    editor.dispatch_event('keydown', {'key': 'Enter'})
    assert sum(c['method'] == 'session.create' for c in state['calls']) == 1
    assert not any(c['method'] == 'session.submit' for c in state['calls'])
    socket, request_id, result = state['pending_create']
    import json
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': result}))
    expect(page.locator('.chat-user')).to_contain_text('只创建一次')
    assert sum(c['method'] == 'session.submit' for c in state['calls']) == 1


def test_create_rejection_keeps_welcome_draft(page):
    state, _ = gateway(page, selected='', sessions=[], reject_create=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('创建失败仍保留')
    editor.press('Enter')
    expect(page.get_by_role('alert')).to_contain_text('创建失败')
    expect(editor).to_have_value('创建失败仍保留')
    expect(editor).to_be_enabled()
    assert not any(c['method'] == 'session.submit' for c in state['calls'])


def test_uncertain_creation_reuses_id_and_does_not_submit_on_reconnect(page):
    state, _ = gateway(page, selected='', sessions=[], disconnect_create=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('核对创建结果')
    editor.press('Enter')
    expect(page.get_by_role('alert')).to_contain_text('创建结果尚未确认')
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=10000)
    assert not any(c['method'] == 'session.submit' for c in state['calls'])
    editor.press('Enter')
    expect(page.locator('.chat-user')).to_contain_text('核对创建结果')
    creates = [c['params'] for c in state['calls'] if c['method'] == 'session.create']
    assert len(creates) == 1
    assert next(c['params']['session_id'] for c in state['calls'] if c['method'] == 'session.submit') == creates[0]['session_id']


def test_created_session_submit_rejection_preserves_first_draft(page):
    state, _ = gateway(page, selected='', sessions=[], reject=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('首条提交失败')
    editor.press('Enter')
    expect(page.get_by_role('alert')).to_contain_text('工作区不可用')
    expect(editor).to_have_value('首条提交失败')
    assert sum(c['method'] == 'session.create' for c in state['calls']) == 1
    assert 'session=' in page.url and not page.url.endswith('session=')
