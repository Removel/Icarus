import json

import pytest
from playwright.sync_api import expect

from test.navigation import open_settings
from test.packages.chat_app.test_chat import gateway


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [390, 1440])
def test_conversation_and_thinking_use_dswui_scrollbars(page, theme, width):
    page.emulate_media(color_scheme=theme)
    page.set_viewport_size({'width': width, 'height': 900})
    _, emit = gateway(page)
    emit('user.message', {'text': '检查滚动条'})
    for step in range(1, 9):
        emit('assistant.thinking', {'step': step, 'text': '思考过程\n' * 20})
    emit('assistant.message', {'step': 9, 'text': '对话正文\n\n' * 60})
    emit('task.finished', {'status': 'completed'})
    expect(page.locator('.chat-thinking')).to_have_count(8)
    page.locator('.chat-process > summary').click()
    page.mouse.move(0, 0)
    for selector in ['.chat-transcript', '.chat-process-items']:
        container = page.locator(selector)
        expect(container).to_be_visible()
        metrics = container.evaluate('''el => {
            const style = getComputedStyle(el);
            const bar = getComputedStyle(el, '::-webkit-scrollbar');
            const thumb = getComputedStyle(el, '::-webkit-scrollbar-thumb');
            const track = getComputedStyle(el, '::-webkit-scrollbar-track');
            el.scrollTop = 20;
            return {width: style.scrollbarWidth, color: style.scrollbarColor,
                barWidth: bar.width, barHeight: bar.height,
                thumbColor: thumb.backgroundColor, radius: thumb.borderRadius,
                track: track.backgroundColor, scrollTop: el.scrollTop,
                overflowing: el.scrollHeight > el.clientHeight};
        }''')
        assert metrics == {
            'width': 'auto', 'color': 'auto', 'barWidth': '8px', 'barHeight': '8px',
            'thumbColor': 'rgba(0, 0, 0, 0.12)' if theme == 'light' else 'rgba(255, 255, 255, 0.14)',
            'radius': '8px', 'track': 'rgba(0, 0, 0, 0)', 'scrollTop': 20,
            'overflowing': True,
        }
    page.locator('.chat-transcript').evaluate('el => el.scrollTop = 0')
    page.screenshot(path=f'test-results/dswui-scrollbars-{theme}-{width}.png')


def test_session_rename_pin_export_and_click_title(page):
    state, emit = gateway(page, sessions=[
        {'session_id': 'session-1', 'title': '原始标题', 'first_user_input': '问题'},
        {'session_id': 'session-2', 'title': '另一段对话', 'first_user_input': ''},
    ])
    emit('user.message', {'text': '问题'})
    emit('assistant.message', {'step': 1, 'text': '回答'})
    page.get_by_role('button', name='会话操作：原始标题', exact=True).click()
    page.get_by_role('menuitem', name='重命名', exact=True).click()
    title = page.get_by_role('textbox', name='会话标题', exact=True)
    title.fill('手动修改后的标题')
    title.press('Enter')
    expect(page.get_by_role('button', name='手动修改后的标题', exact=True)).to_be_visible()
    page.get_by_role('button', name='修改对话标题', exact=True).click()
    title = page.get_by_role('textbox', name='修改对话标题', exact=True)
    title.fill('点击标题修改')
    title.press('Enter')
    expect(page.get_by_role('button', name='点击标题修改', exact=True)).to_be_visible()
    page.get_by_role('button', name='会话操作：点击标题修改', exact=True).click()
    page.get_by_role('menuitem', name='置顶', exact=True).click()
    expect(page.locator('.session-group-title').first).to_have_text('置顶')
    page.get_by_role('button', name='会话操作：点击标题修改', exact=True).click()
    with page.expect_download() as download:
        page.get_by_role('menuitem', name='导出 Markdown', exact=True).click()
    assert download.value.suggested_filename == '点击标题修改.md'
    assert '## 你' in open(download.value.path(), encoding='utf-8').read()
    page.reload()
    expect(page.get_by_role('button', name='点击标题修改', exact=True)).to_be_visible()
    expect(page.locator('.session-group-title').first).to_have_text('置顶')
    assert len([call for call in state['calls'] if call['method'] == 'session.rename']) == 2


def test_markdown_math_line_breaks_and_message_actions(page):
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    state, emit = gateway(page)
    emit('user.message', {'text': '最初的问题'})
    expect(page.locator('.chat-title-edit')).to_have_text('测试会话')
    assert page.locator('.chat-title-edit').evaluate('el => el.getBoundingClientRect().height') <= 22
    emit('assistant.message', {'step': 1, 'text': '第一行\n第二行\n\n公式 $x^2$\n\n| 列 |\n| --- |\n| 内容 |'})
    expect(page.locator('.chat-assistant br')).to_have_count(1)
    expect(page.locator('.chat-assistant .katex')).to_be_visible()
    expect(page.locator('.md-table-wrap table')).to_be_visible()
    expect(page.locator('.assistant-actions button')).to_have_count(6)
    page.screenshot(path='test-results/refinement-messages.png', full_page=True)
    page.get_by_role('button', name='有帮助', exact=True).click()
    expect(page.get_by_role('button', name='有帮助', exact=True)).to_have_attribute('aria-pressed', 'true')
    page.locator('.msg-user').hover()
    page.get_by_role('button', name='复制消息', exact=True).click()
    assert page.evaluate('navigator.clipboard.readText()') == '最初的问题'
    page.get_by_role('button', name='编辑为新消息', exact=True).click()
    expect(page.get_by_role('textbox', name='发送消息')).to_have_value('最初的问题')
    assert not any(call['method'] == 'session.submit' for call in state['calls'])


def test_session_switch_reuses_cache_and_requests_only_updates(page):
    records = [
        {'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
         'type': 'user.message', 'payload': {'text': '已缓存的问题'}, 'sequence': 1},
        {'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
         'type': 'assistant.message', 'payload': {'step': 1, 'text': '已缓存的回答\n' + '对话正文\n' * 60}, 'sequence': 2},
    ]
    state, _ = gateway(page, records=records, sessions=[
        {'session_id': 'session-1', 'title': '第一段', 'first_user_input': '已缓存的问题'},
        {'session_id': 'session-2', 'title': '第二段', 'first_user_input': ''},
    ])
    expect(page.locator('.chat-assistant')).to_contain_text('已缓存的回答')
    page.get_by_role('button', name='第二段', exact=True).click()
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    expect(page.locator('.chat-assistant')).to_have_count(0)
    state['hold_history'] = True
    page.expose_function('pending_history_count', lambda: len(state.get('pending_history', [])))
    frame = page.get_by_role('button', name='第一段', exact=True).evaluate('''el => {
        el.click();
        return new Promise(resolve => requestAnimationFrame(() => {
            const log = document.querySelector('.chat-transcript');
            resolve({text: log.textContent, top: log.scrollTop,
                     bottom: log.scrollHeight - log.clientHeight});
        }));
    }''')
    assert '已缓存的回答' in frame['text']
    assert frame['top'] > 0
    assert abs(frame['top'] - frame['bottom']) < 2
    expect(page.locator('.chat-assistant')).to_contain_text('已缓存的回答')
    # Cached content paints before the asynchronous status/subscribe/history RPCs finish.
    page.wait_for_function('async () => await window.pending_history_count() === 1')
    reads = [call['params'] for call in state['calls'] if call['method'] == 'session.get_history' and call['params']['session_id'] == 'session-1']
    assert [read['after_sequence'] for read in reads] == [0, 2]
    socket, request_id = state['pending_history'][0]
    update = {**records[1], 'payload': {'step': 2, 'text': '增量回答'}, 'sequence': 3}
    socket.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': {
        'records': [update], 'has_more': False, 'next_after_sequence': 3, 'history_cursor': 3}}))
    expect(page.locator('.chat-assistant')).to_have_count(2)
    expect(page.locator('.chat-assistant').first).to_contain_text('已缓存的回答')
    expect(page.locator('.chat-assistant').last).to_contain_text('增量回答')
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()


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
    steps = settings.get_by_role('spinbutton', name='最大执行步数', exact=True)
    expect(steps).not_to_be_visible()
    expect(settings.get_by_role('checkbox', name='允许生成 Skill')).to_be_visible()
    settings.get_by_text('高级执行设置', exact=True).click()
    steps.fill('64')
    assert steps.evaluate('el => getComputedStyle(el).outlineStyle') == 'none'
    scroll = settings.locator('.settings-content')
    assert scroll.evaluate('el => getComputedStyle(el).scrollbarWidth') == 'auto'
    assert scroll.evaluate('el => getComputedStyle(el, "::-webkit-scrollbar").width') == '8px'
    settings.get_by_role('button', name='保存配置', exact=True).click()
    expect(settings.get_by_role('status')).to_contain_text('已保存')
    assert snapshot['settings']['agent']['max_steps'] == 64
    page.screenshot(path='test-results/refinement-agent-settings.png', full_page=True)
    settings.get_by_role('button', name='模型配置', exact=True).click()
    expect(settings.get_by_role('combobox', name='模型角色 agent')).to_be_visible()
    expect(settings.get_by_role('textbox', name='模型 1 ID', exact=True)).not_to_be_visible()
    settings.get_by_text('高级模型配置', exact=True).click()
    settings.get_by_role('textbox', name='模型 1 ID', exact=True).fill('new-model')
    settings.get_by_role('textbox', name='模型 1 名称', exact=True).fill('renamed-main')
    expect(settings.get_by_role('combobox', name='模型角色 title')).to_contain_text('renamed-main')
    expect(settings.get_by_role('combobox', name='模型 1 思考强度', exact=True)).to_have_count(0)
    expect(settings.get_by_role('button', name='移除模型', exact=True)).to_be_disabled()
    settings.get_by_role('button', name='保存配置', exact=True).click()
    expect(settings.get_by_role('status')).to_contain_text('已保存')
    assert snapshot['settings']['model_settings'][0]['model_name'] == 'new-model'
    assert snapshot['settings']['model_settings'][0]['default_think_level'] == 'medium'
    assert set(snapshot['settings']['model_roles'].values()) == {'renamed-main'}
    assert snapshot['settings']['agent']['tool_execution']['default_timeout_seconds'] == 120
    assert snapshot['settings']['agent']['max_steps'] == 64
