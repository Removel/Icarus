from datetime import datetime, timedelta, timezone
import json

import pytest
from playwright.sync_api import expect

from test.navigation import open_sessions
from test.packages.chat_app.test_chat import gateway


def sessions():
    now = datetime.now(timezone.utc)
    return [dict(session_id=f'session-{i}', title=title, first_user_input=title,
                 created_at=(now - timedelta(days=2)).isoformat(),
                 updated_at=(now - timedelta(minutes=i)).isoformat())
            for i, title in [(2, '较新会话'), (1, '当前会话')]]


def test_sessions_sort_on_load_and_after_accepted_submission(page):
    rows = sessions()
    rows[0]['updated_at'], rows[1]['updated_at'] = rows[1]['updated_at'], rows[0]['updated_at']
    gateway(page, sessions=list(reversed(rows)))
    open_sessions(page)
    expect(page.locator('.chat-session')).to_have_text(['较新会话', '当前会话'])
    page.get_by_role('textbox', name='发送消息').fill('继续旧对话')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.locator('.chat-session')).to_have_text(['当前会话', '较新会话'])


def test_runtime_activity_moves_session_but_late_events_do_not_move_it_back(page):
    rows = sessions()
    rows[0]['updated_at'], rows[1]['updated_at'] = rows[1]['updated_at'], rows[0]['updated_at']
    state, emit = gateway(page, sessions=rows)
    open_sessions(page)
    emit('assistant.text_delta', {'step': 1, 'text': '继续回答'})
    record = dict(state['records'][-1], sequence=100,
                  occurred_at=datetime.now(timezone.utc).isoformat())
    state['sockets'][-1].send(json.dumps(
        {'jsonrpc': '2.0', 'method': 'runtime.update', 'params': record}))
    expect(page.locator('.chat-session')).to_have_text(['当前会话', '较新会话'])
    record.update(sequence=101, occurred_at='2000-01-01T00:00:00Z')
    state['sockets'][-1].send(json.dumps(
        {'jsonrpc': '2.0', 'method': 'runtime.update', 'params': record}))
    expect(page.locator('.chat-session')).to_have_text(['当前会话', '较新会话'])


@pytest.mark.parametrize('width', [390, 1440])
def test_live_thought_is_visible_and_manual_collapse_survives_updates(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    _, emit = gateway(page)
    emit('task.started', {})
    emit('assistant.thinking_delta', {'step': 1, 'text': '实时分析可见'})
    panel = page.locator('.chat-reasoning-text')
    expect(panel).to_be_visible()
    expect(panel).to_have_text('实时分析可见')
    page.screenshot(path=f'test-results/live-thought-default-{width}.png')
    page.locator('.chat-process > summary').click()
    expect(panel).not_to_be_visible()
    emit('assistant.thinking_delta', {'step': 1, 'text': '，继续分析'})
    emit('task.finished', {'status': 'completed'})
    expect(panel).not_to_be_visible()
    page.locator('.chat-process > summary').click()
    expect(panel).to_contain_text('继续分析')
    page.reload()
    expect(page.locator('.chat-process')).not_to_have_attribute('open', '')


def test_ending_task_keeps_live_thought_open(page):
    _, emit = gateway(page)
    emit('task.started', {})
    emit('assistant.thinking_delta', {'step': 1, 'text': '保留阅读位置'})
    expect(page.locator('.chat-reasoning-text')).to_be_visible()
    emit('assistant.thinking', {'step': 1, 'text': '保留阅读位置'})
    emit('task.finished', {'status': 'completed'})
    expect(page.locator('.chat-reasoning-text')).to_be_visible()


def test_rejected_submission_does_not_promote_an_old_session(page):
    rows = sessions()
    rows[0]['updated_at'], rows[1]['updated_at'] = rows[1]['updated_at'], rows[0]['updated_at']
    gateway(page, sessions=rows, reject=True)
    page.get_by_role('textbox', name='发送消息').fill('会被拒绝')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('工作区不可用')
    expect(page.locator('.chat-session')).to_have_text(['较新会话', '当前会话'])


def test_invalid_dates_fallback_and_ties_sort_stably(page):
    now = datetime.now(timezone.utc).isoformat()
    rows = [dict(session_id='session-2', title='同时间乙', updated_at=now),
            dict(session_id='session-1', title='同时间甲', updated_at=now),
            dict(session_id='fallback', title='回退创建时间', updated_at='invalid', created_at='2020-01-01T00:00:00Z'),
            dict(session_id='invalid', title='无效时间', updated_at='invalid')]
    gateway(page, sessions=rows)
    expect(page.locator('.chat-session')).to_have_text(['同时间甲', '同时间乙', '回退创建时间', '无效时间'])


def test_pinned_sessions_remain_first_and_sort_inside_group(page):
    page.add_init_script('''localStorage.setItem('icarus.chat.pins', JSON.stringify({
      '/tmp/webui-test': ['session-2', 'session-1']
    }))''')
    rows = sessions()
    rows.append(dict(session_id='session-3', title='最新未置顶', updated_at=datetime.now(timezone.utc).isoformat()))
    gateway(page, sessions=rows)
    expect(page.locator('.session-group-title').first).to_have_text('置顶')
    expect(page.locator('.chat-session')).to_have_text(['当前会话', '较新会话', '最新未置顶'])


def test_manual_collapse_survives_virtual_list_remount(page):
    _, emit = gateway(page)
    emit('user.message', {'text': '最早问题'})
    emit('assistant.thinking_delta', {'step': 1, 'text': '最早思考'})
    expect(page.locator('.chat-reasoning-text')).to_be_visible()
    page.locator('.chat-process > summary').click()
    emit('task.finished', {'status': 'completed'})
    for index in range(2, 44):
        task = f'task-{index}'
        emit('user.message', {'text': f'问题 {index}'}, task_id=task)
        emit('assistant.message', {'step': 1, 'text': '完整回答。' * 20}, task_id=task)
    expect(page.locator('[data-virtualized]')).to_have_count(1)
    page.get_by_role('navigation', name='对话目录').get_by_role('button', name='最早问题', exact=True).click()
    expect(page.locator('.chat-process')).to_be_attached()
    expect(page.locator('.chat-process')).not_to_have_attribute('open', '')
