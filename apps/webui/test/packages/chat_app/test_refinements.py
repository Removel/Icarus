import json

import pytest
from playwright.sync_api import expect

from test.navigation import open_settings
from test.packages.chat_app.test_chat import gateway


def test_session_switch_reuses_cache_and_requests_only_updates(page):
    records = [
        {'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
         'type': 'user.message', 'payload': {'text': '已缓存的问题'}, 'sequence': 1},
        {'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
         'type': 'assistant.message', 'payload': {'step': 1, 'text': '已缓存的回答'}, 'sequence': 2},
    ]
    state, _ = gateway(page, records=records, sessions=[
        {'session_id': 'session-1', 'title': '第一段', 'first_user_input': '已缓存的问题'},
        {'session_id': 'session-2', 'title': '第二段', 'first_user_input': ''},
    ])
    expect(page.locator('.chat-assistant')).to_contain_text('已缓存的回答')
    page.get_by_role('button', name='第二段', exact=True).click()
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    page.get_by_role('button', name='第一段', exact=True).click()
    expect(page.locator('.chat-assistant')).to_contain_text('已缓存的回答')
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    reads = [call['params'] for call in state['calls'] if call['method'] == 'session.get_history' and call['params']['session_id'] == 'session-1']
    assert reads[-1]['after_sequence'] == 2


def test_agent_and_model_settings_share_server_configuration(page):
    state, _ = gateway(page)
    snapshot = {'revision': 'one', 'settings': {
        'agent': {'max_steps': 256, 'tool_execution': {'max_calls_per_batch': 8, 'default_timeout_seconds': 120, 'max_timeout_seconds': 600, 'default_output_tokens': 4000, 'batch_output_tokens': 16000}},
        'skill': {'allow_produce': False, 'allow_evolve': False},
        'model_settings': [{'name': 'main', 'model_name': 'existing-model', 'context_window': 65536, 'max_tokens': 8192, 'temperature': 0.6, 'default_think_level': 'medium'}],
        'model_roles': {'agent': 'main', 'title': 'main', 'compaction': 'main', 'skill_generation': 'main'},
    }}
    def reply(socket):
        def receive(raw):
            call = json.loads(raw)
            state['calls'].append(call)
            if call['method'] == 'settings.update':
                snapshot.update(revision='two', settings=call['params']['settings'])
            socket.send(json.dumps({'jsonrpc': '2.0', 'id': call['id'], 'result': snapshot}))
        socket.on_message(receive)
    # Settings reuses the established chat socket; intercept through a fresh page without a workspace.
    page.route_web_socket('**/rpc', reply)
    page.goto(page.url.split('#')[0] + '#/chat')
    open_settings(page)
    settings = page.get_by_role('dialog', name='设置', exact=True)
    settings.get_by_role('button', name='Agent 与工具', exact=True).click()
    settings.get_by_role('spinbutton', name='最大执行步数', exact=True).fill('64')
    settings.get_by_role('button', name='保存配置', exact=True).click()
    expect(settings.get_by_role('status')).to_contain_text('已保存')
    assert snapshot['settings']['agent']['max_steps'] == 64
    page.screenshot(path='test-results/refinement-agent-settings.png', full_page=True)
    settings.get_by_role('button', name='模型配置', exact=True).click()
    settings.get_by_role('textbox', name='模型 1 ID', exact=True).fill('new-model')
    settings.get_by_role('button', name='保存配置', exact=True).click()
    expect(settings.get_by_role('status')).to_contain_text('已保存')
    assert snapshot['settings']['model_settings'][0]['model_name'] == 'new-model'
    assert snapshot['settings']['agent']['max_steps'] == 64
