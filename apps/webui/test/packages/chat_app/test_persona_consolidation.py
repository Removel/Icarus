import re

import pytest
from playwright.sync_api import expect

from test.navigation import open_sessions
from test.packages.chat_app.test_chat import gateway


def consolidation_calls(state):
    return [call for call in state['calls'] if call['method'] == 'session.consolidate_persona']


@pytest.mark.parametrize('mode', ['slow', 'error', 'old_gateway'])
def test_new_chat_requests_old_identity_without_waiting_or_showing_errors(page, mode):
    state, _ = gateway(page)
    state['hold_consolidation'] = mode == 'slow'
    state['reject_consolidation'] = mode != 'slow'
    state['consolidation_error_code'] = -32601 if mode == 'old_gateway' else -32000
    page.get_by_role('button', name='新建会话').click()
    expect(page).not_to_have_url(re.compile('session=session-1'))
    page.wait_for_function("window.location.hash.includes('workspace=') && !window.location.hash.includes('session=')")
    assert len(consolidation_calls(state)) == 1
    assert consolidation_calls(state)[0]['params'] == {
        'workspace_path': '/tmp/webui-test', 'session_id': 'session-1',
    }
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('新会话消息')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.get_by_role('button', name='停止执行')).to_be_visible()
    assert any(call['method'] == 'session.submit' for call in state['calls'])
    expect(page.get_by_role('alert')).to_have_count(0)


def test_cancelled_leave_and_plain_history_navigation_do_not_request_consolidation(page):
    state, _ = gateway(page, sessions=[
        {'session_id': 'session-1', 'first_user_input': '第一段'},
        {'session_id': 'session-2', 'first_user_input': '第二段'},
    ])
    page.get_by_role('textbox', name='发送消息').fill('保留草稿')
    page.get_by_role('button', name='新建会话').click()
    dialog = page.get_by_role('dialog', name='放弃未发送的消息？')
    expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='继续编辑', exact=True).click()
    expect(page.get_by_role('textbox', name='发送消息')).to_have_value('保留草稿')
    assert not consolidation_calls(state)
    page.get_by_role('textbox', name='发送消息').fill('')
    open_sessions(page)
    page.get_by_role('button', name='第二段', exact=True).click()
    expect(page).to_have_url(re.compile('session=session-2'))
    assert not consolidation_calls(state)


def test_new_chat_from_blank_page_never_requests_a_source(page):
    state, _ = gateway(page, selected='')
    page.get_by_role('button', name='新建会话').click()
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    assert not consolidation_calls(state)


@pytest.mark.parametrize('entry', ['shortcut', 'accepted_draft', 'mobile'])
def test_existing_new_chat_entries_request_the_captured_source_once(page, entry):
    if entry == 'mobile':
        page.set_viewport_size({'width': 390, 'height': 844})
    state, _ = gateway(page)
    if entry == 'shortcut':
        page.keyboard.press('Control+j')
    else:
        if entry == 'accepted_draft':
            page.get_by_role('textbox', name='发送消息').fill('未发送的草稿')
        open_sessions(page)
        page.get_by_role('button', name='新建会话').click()
        if entry == 'accepted_draft':
            page.get_by_role('dialog', name='放弃未发送的消息？').get_by_role('button', name='放弃', exact=True).click()
    expect(page).not_to_have_url(re.compile('session=session-1'))
    assert len(consolidation_calls(state)) == 1
    assert consolidation_calls(state)[0]['params']['session_id'] == 'session-1'
    expect(page.get_by_role('textbox', name='发送消息')).to_have_value('')


def test_disconnected_new_chat_does_not_send_or_retry_consolidation(page):
    state, _ = gateway(page)
    state['sockets'][-1].close()
    expect(page.get_by_role('alert')).to_contain_text('连接已断开')
    page.get_by_role('button', name='新建会话').click()
    expect(page).not_to_have_url(re.compile('session=session-1'))
    assert not consolidation_calls(state)
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=10000)
    assert not consolidation_calls(state)
