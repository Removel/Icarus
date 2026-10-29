import json

import pytest
from playwright.sync_api import expect

from test.navigation import open_settings
from test.packages.chat_app.test_chat import gateway


KEY = 'icarus.chat.workspaceHistory'


def workspace_settings(page):
    expect(page.get_by_role('region', name='Agent 对话', exact=True)).to_be_visible()
    open_settings(page)
    modal = page.get_by_role('dialog', name='设置', exact=True)
    modal.get_by_role('button', name='工作区', exact=True).click()
    return modal


def expect_history(page, paths):
    page.wait_for_function(
        '([key, paths]) => localStorage.getItem(key) === JSON.stringify(paths)',
        arg=[KEY, paths],
    )


def test_workspace_history_keeps_five_recent_paths_and_restores_selection(page):
    state, _ = gateway(page, selected='', sessions=[])
    expect_history(page, ['/tmp/webui-test'])
    paths = [f'/tmp/recent-{index}' for index in range(6)]
    for index, path in enumerate(paths):
        modal = workspace_settings(page)
        modal.get_by_role('textbox', name='工作区路径', exact=True).fill('  ' + path + '  ')
        modal.get_by_role('button', name='连接工作区', exact=True).click()
        expect_history(page, list(reversed(paths[:index + 1]))[:5] +
                       (['/tmp/webui-test'] if index < 4 else []))

    page.reload()
    modal = workspace_settings(page)
    modal.get_by_role('combobox', name='工作区历史记录', exact=True).click()
    expect(page.get_by_role('option')).to_have_text(list(reversed(paths[1:])))
    page.get_by_role('option', name=paths[2], exact=True).click()
    expect(modal.get_by_role('textbox', name='工作区路径', exact=True)).to_have_value(paths[2])
    assert 'recent-5' in page.url
    modal.get_by_role('button', name='连接工作区', exact=True).click()
    expect_history(page, [paths[2], paths[5], paths[4], paths[3], paths[1]])
    assert all(KEY not in json.dumps(call) for call in state['calls'])


def test_failed_workspace_connection_does_not_enter_history(page):
    gateway(page, selected='', sessions=[])
    expect_history(page, ['/tmp/webui-test'])

    def reject_workspace(socket):
        def receive(raw):
            call = json.loads(raw)
            socket.send(json.dumps({
                'jsonrpc': '2.0', 'id': call['id'],
                'error': {'code': -32000, 'message': '工作区不可用'},
            }))
        socket.on_message(receive)

    page.route_web_socket('**/rpc', reject_workspace)
    modal = workspace_settings(page)
    modal.get_by_role('textbox', name='工作区路径', exact=True).fill('/tmp/missing')
    modal.get_by_role('button', name='连接工作区', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('工作区不可用')
    expect_history(page, ['/tmp/webui-test'])
    modal = workspace_settings(page)
    modal.get_by_role('combobox', name='工作区历史记录', exact=True).click()
    expect(page.get_by_role('option')).to_have_text(['/tmp/webui-test'])


@pytest.mark.parametrize('stored, expected', [
    ('{broken', []),
    ('{"path": "/tmp/invalid"}', []),
    (json.dumps([None, 7, '  F:\\项目\\Icarus  ', '', 'F:\\项目\\Icarus',
                 '/a', '/b', '/c', '/d', '/e']), ['F:\\项目\\Icarus', '/a', '/b', '/c', '/d']),
], ids=['invalid-json', 'invalid-shape', 'mixed-paths'])
def test_workspace_history_recovers_from_invalid_local_data(page, stored, expected):
    page.goto('/#/chat')
    page.evaluate('([key, value]) => localStorage.setItem(key, value)', [KEY, stored])
    page.reload()
    modal = workspace_settings(page)
    expect_history(page, expected)
    history = modal.get_by_role('combobox', name='工作区历史记录', exact=True)
    if expected:
        history.click()
        expect(page.get_by_role('option')).to_have_text(expected)
    else:
        expect(history).to_have_count(0)


def test_workspace_history_works_when_local_storage_is_unavailable(page):
    page.add_init_script('''(() => {
        for (const method of ['getItem', 'setItem']) {
            const original = Storage.prototype[method];
            Storage.prototype[method] = function(key, ...args) {
                if (key === 'icarus.chat.workspaceHistory') throw new Error('Storage unavailable');
                return original.call(this, key, ...args);
            };
        }
    })()''')
    gateway(page, selected='', sessions=[])
    modal = workspace_settings(page)
    modal.get_by_role('combobox', name='工作区历史记录', exact=True).click()
    expect(page.get_by_role('option')).to_have_text(['/tmp/webui-test'])
    page.get_by_role('option', name='/tmp/webui-test', exact=True).click()
    modal.get_by_role('textbox', name='工作区路径', exact=True).fill('/tmp/local-only')
    modal.get_by_role('button', name='连接工作区', exact=True).click()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible()
    modal = workspace_settings(page)
    modal.get_by_role('combobox', name='工作区历史记录', exact=True).click()
    expect(page.get_by_role('option')).to_have_text(['/tmp/local-only', '/tmp/webui-test'])
