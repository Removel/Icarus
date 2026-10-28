import pytest
from playwright.sync_api import expect

from test.navigation import open_sessions
from test.packages.chat_app.test_chat import gateway


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [390, 1440])
def test_rename_uses_only_a_thin_focus_border(page, theme, width):
    page.set_viewport_size({'width': width, 'height': 900})
    gateway(page)
    page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
    open_sessions(page)
    page.get_by_role('button', name='会话操作：测试会话', exact=True).click()
    page.get_by_role('menuitem', name='重命名', exact=True).click()
    editor = page.get_by_role('textbox', name='会话标题', exact=True)
    expect(editor).to_be_focused()
    style = editor.evaluate('''node => { const s = getComputedStyle(node);
        return {shadow: s.boxShadow, outline: s.outlineStyle, border: s.borderWidth}; }''')
    page.screenshot(path=f'test-results/rename-{theme}-{width}.png')
    assert style == {'shadow': 'none', 'outline': 'none', 'border': '1px'}
    expect(page.locator('.session-item.is-renaming')).to_have_css('background-color', 'rgba(0, 0, 0, 0)')
    row = page.locator('.session-item.is-renaming').bounding_box()
    box = editor.bounding_box()
    assert box['height'] == 40
    assert box == row


@pytest.mark.parametrize('width', [390, 1440])
def test_tool_timeline_keeps_input_result_and_terminal_status(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    _, emit = gateway(page)
    emit('user.message', {'text': '读取资料'})
    emit('task.started', {})
    emit('tool.started', {'call_id': 'one', 'tool_name': 'read_file', 'arguments': {'path': 'notes.md'}})
    page.locator('.chat-process > summary').click()
    tool = page.locator('.chat-tool')
    expect(tool).to_have_attribute('data-status', 'running')
    tool.locator('summary').click()
    expect(tool.locator('pre')).to_contain_text('notes.md')
    emit('tool.completed', {'call_id': 'one', 'tool_name': 'read_file', 'success': False, 'error': '文件不存在'})
    expect(tool).to_have_attribute('data-status', 'error')
    expect(tool.locator('pre').first).to_contain_text('notes.md')
    expect(tool.locator('pre').last).to_have_text('文件不存在')
    tool.locator('summary').click()
    emit('tool.started', {'call_id': 'two', 'tool_name': 'read_file', 'arguments': {}})
    emit('task.finished', {'status': 'cancelled'})
    expect(page.locator('.chat-tool').last).to_have_attribute('data-status', 'stopped')
    page.screenshot(path=f'test-results/tool-timeline-{width}.png')
    page.reload()
    page.locator('.chat-process > summary').click()
    expect(page.locator('.chat-tool').last).to_have_attribute('data-status', 'stopped')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('message,expected', [
    ('AuthenticationError: 401 private api key', '模型服务认证失败'),
    ('RateLimitError: 429 private response', '模型服务请求过于频繁'),
    ('APITimeoutError: request timed out', '本次回答等待超时'),
    ('ConnectError: connection refused private address', '模型服务暂时无法连接'),
])
def test_model_errors_are_actionable_and_keep_generated_text(page, message, expected):
    _, emit = gateway(page)
    emit('user.message', {'text': '问题'})
    emit('assistant.message', {'step': 1, 'text': '已有部分回答'})
    emit('task.error', {'message': message})
    emit('task.finished', {'status': 'failed'})
    expect(page.locator('.error-card')).to_contain_text(expected)
    expect(page.locator('.error-card')).not_to_contain_text('private')
    expect(page.locator('.chat-assistant')).to_contain_text('已有部分回答')
