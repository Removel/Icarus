import pytest
from playwright.sync_api import expect

from test.navigation import open_sessions, open_settings
from test.packages.chat_app.test_chat import gateway


SESSIONS = [
    {'session_id': 'session-1', 'title': '第一段', 'first_user_input': ''},
    {'session_id': 'session-2', 'title': '第二段', 'first_user_input': ''},
    {'session_id': 'session-3', 'title': '第三段', 'first_user_input': ''},
]


@pytest.mark.parametrize('width', [390, 1440])
@pytest.mark.parametrize('title', ['测试会话', '较长的会话标题' * 10])
def test_navigation_icons_align_in_open_and_collapsed_sidebar(page, width, title):
    page.set_viewport_size({'width': width, 'height': 900})
    _, emit = gateway(page, sessions=[{'session_id': 'session-1', 'title': title, 'first_user_input': ''}])
    emit('user.message', {'text': '开始对话'})
    expect(page.locator('.chat-title-edit')).to_be_visible()
    open_sessions(page)
    for selector in ['.brand-actions', '.collapsed-actions']:
        if selector == '.collapsed-actions':
            page.get_by_role('button', name='收起会话列表', exact=True).click()
        icons = page.locator(selector + ' .icon-btn > svg')
        expect(icons.first).to_be_visible()
        bounds = icons.evaluate_all('nodes => nodes.map(el => { const r = el.getBoundingClientRect(); return { y: r.y, height: r.height, width: r.width }; })')
        assert len(bounds) >= 2 and all(box == bounds[0] for box in bounds)
        assert bounds[0]['height'] == 16
        title_box = page.locator('.chat-title-edit').bounding_box()
        icon_center = bounds[0]['y'] + bounds[0]['height'] / 2
        assert abs(title_box['y'] + title_box['height'] / 2 - icon_center) < 1, (title_box, bounds)
        if width >= 768 or selector == '.collapsed-actions':
            page.get_by_role('button', name='修改对话标题', exact=True).click()
            editor = page.get_by_role('textbox', name='修改对话标题', exact=True)
            box = editor.bounding_box()
            assert abs(box['y'] + box['height'] / 2 - icon_center) < 1, (box, bounds)
            editor.press('Escape')
        page.screenshot(path=f'test-results/header-alignment-{width}-{len(title)}-{selector[1:]}.png',
                        clip={'x': 0, 'y': 0, 'width': width, 'height': 80})


@pytest.mark.parametrize('width', [390, 1440])
def test_multi_select_pin_cancel_and_delete(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    state, _ = gateway(page, sessions=[dict(row) for row in SESSIONS])
    open_sessions(page)
    page.get_by_role('button', name='多选', exact=True).click()
    page.get_by_role('button', name='全选', exact=True).click()
    expect(page.get_by_text('已选择 3 个对话', exact=True)).to_be_visible()
    page.get_by_role('checkbox', name='选择对话：第三段', exact=True).click()
    expect(page.get_by_text('已选择 2 个对话', exact=True)).to_be_visible()
    page.locator('.session-batch-actions').get_by_role('button', name='置顶', exact=True).click()
    pin = page.get_by_role('dialog', name='将选择的 2 个对话置顶？', exact=True)
    pin.get_by_role('button', name='置顶', exact=True).click()
    expect(page.get_by_role('button', name='多选', exact=True)).to_be_visible()
    expect(page.locator('.session-group-title').first).to_have_text('置顶')
    page.get_by_role('button', name='多选', exact=True).click()
    # Clicking a row in selection mode selects it without navigating.
    page.get_by_role('button', name='第一段', exact=True).click()
    page.get_by_role('button', name='第二段', exact=True).click()
    assert 'session=session-1' in page.url
    page.locator('.session-batch-actions').get_by_role('button', name='删除', exact=True).click()
    modal = page.get_by_role('dialog', name='删除选择的 2 个对话？', exact=True)
    modal.get_by_role('button', name='取消', exact=True).click()
    assert not any(call['method'] == 'session.delete' for call in state['calls'])
    page.locator('.session-batch-actions').get_by_role('button', name='删除', exact=True).click()
    modal.get_by_role('button', name='删除', exact=True).click()
    expect(page.get_by_role('button', name='第一段', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='第二段', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='第三段', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='多选', exact=True)).to_be_visible()
    page.wait_for_url('**/*session=session-3')
    deleted = [call['params']['session_id'] for call in state['calls'] if call['method'] == 'session.delete']
    assert deleted == ['session-1', 'session-2']
    page.reload()
    open_sessions(page)
    expect(page.get_by_role('button', name='第三段', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='第一段', exact=True)).to_have_count(0)


def test_batch_delete_partial_failure_keeps_remaining_selection(page):
    state, _ = gateway(page, sessions=[dict(row) for row in SESSIONS])
    state['delete_status'] = {'session-2': 'busy'}
    page.get_by_role('button', name='多选', exact=True).click()
    page.get_by_role('button', name='全选', exact=True).click()
    page.locator('.session-batch-actions').get_by_role('button', name='删除', exact=True).click()
    page.get_by_role('dialog', name='删除选择的 3 个对话？', exact=True).get_by_role('button', name='删除', exact=True).click()
    modal = page.get_by_role('dialog', name='删除选择的 2 个对话？', exact=True)
    expect(modal.get_by_role('alert')).to_contain_text('会话正在执行')
    assert [row['session_id'] for row in state['sessions']] == ['session-2', 'session-3']
    state['delete_status'] = {}
    modal.get_by_role('button', name='删除', exact=True).click()
    expect(modal).not_to_be_visible()
    expect(page.get_by_text('暂无历史对话', exact=True)).to_be_visible()
    assert state['sessions'] == []


def test_selection_preserves_draft_and_escape_exits(page):
    state, _ = gateway(page, sessions=[dict(row) for row in SESSIONS])
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('未发送的内容')
    page.get_by_role('button', name='会话操作：第一段', exact=True).click()
    page.get_by_role('menuitem', name='多选', exact=True).click()
    expect(page.get_by_role('checkbox', name='选择对话：第一段', exact=True)).to_be_checked()
    expect(page.locator('.session-batch-actions').get_by_role('button', name='删除', exact=True)).to_be_disabled()
    page.get_by_role('button', name='第二段', exact=True).click()
    expect(editor).to_have_value('未发送的内容')
    assert 'session=session-1' in page.url
    page.keyboard.press('Escape')
    expect(page.locator('.session-batch-actions')).to_have_count(0)
    assert not any(call['method'] == 'session.delete' for call in state['calls'])


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_dswui_fields_navigation_and_settings_dropdown(page, theme):
    page.emulate_media(color_scheme=theme)
    _, emit = gateway(page, sessions=[dict(row) for row in SESSIONS])
    emit('user.message', {'text': '开始对话'})
    expect(page.locator('.chat-management-links')).to_have_count(0)
    page.get_by_role('button', name='账户菜单', exact=True).click()
    menu = page.get_by_role('menu')
    for label in ('知识库', '记忆', '人物'):
        expect(menu.get_by_role('menuitem', name=label, exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    assert page.locator('.session-region').evaluate('el => parseFloat(getComputedStyle(el).marginTop)') == 12
    expect(page.locator('.brand .icarus-brand-mark')).to_have_attribute('src', '/icarus.svg')
    expect(page.locator('.brand .icarus-wordmark')).to_have_attribute('src', '/icarus-wordmark.svg')
    expect(page.locator('link[rel="icon"]')).to_have_attribute('href', '/icarus.svg')
    expect(page.locator('.chat-workspace-trigger')).to_have_count(0)
    icons = page.locator('.brand-actions .icon-btn > svg').evaluate_all('''nodes => nodes.map(el => {
        const box = el.getBoundingClientRect();
        return {y: box.y, height: box.height, width: box.width, display: getComputedStyle(el).display};
    })''')
    assert len(icons) == 2
    assert icons[0] == icons[1]
    assert icons[0]['height'] == 16 and icons[0]['display'] == 'block'
    page.get_by_role('button', name='搜索对话', exact=True).click()
    search = page.get_by_role('textbox', name='搜索会话…')
    expect(search).to_be_focused()
    assert search.evaluate('el => getComputedStyle(el).outlineStyle') == 'none'
    search.fill('第二段')
    expect(page.get_by_role('dialog', name='搜索对话').get_by_role('button', name='第二段', exact=True)).to_be_visible()
    page.screenshot(path=f'test-results/controls-search-{theme}.png')
    page.keyboard.press('Escape')
    page.get_by_role('button', name='会话操作：第一段', exact=True).click()
    page.get_by_role('menuitem', name='重命名', exact=True).click()
    rename = page.get_by_role('textbox', name='会话标题', exact=True)
    assert rename.evaluate('el => getComputedStyle(el).outlineStyle') == 'none'
    assert rename.bounding_box()['height'] == 40
    assert rename.evaluate('el => getComputedStyle(el).color') == page.locator('.chat-title-edit').evaluate('el => getComputedStyle(el).color')
    page.screenshot(path=f'test-results/controls-rename-{theme}.png')
    rename.press('Escape')
    page.get_by_role('button', name='修改对话标题', exact=True).click()
    heading = page.get_by_role('textbox', name='修改对话标题', exact=True)
    assert heading.bounding_box()['height'] == 40
    assert heading.evaluate('el => getComputedStyle(el).outlineStyle') == 'none'
    editor_box = page.locator('.conversation-title-editor').bounding_box()
    assert editor_box['width'] > 26
    assert abs(heading.bounding_box()['x'] - editor_box['x']) < 1
    heading.fill('更长的标题用来验证输入框随文字扩展')
    assert page.locator('.conversation-title-editor').bounding_box()['width'] > editor_box['width']
    heading.press('Escape')
    open_settings(page)
    settings = page.get_by_role('dialog', name='设置', exact=True)
    select = settings.get_by_role('combobox', name='对话字号', exact=True)
    assert select.evaluate('el => getComputedStyle(el).borderRadius') == '18px'
    select.click()
    option = settings.get_by_role('option', name='18 px', exact=True)
    expect(option).to_be_visible()
    page.screenshot(path=f'test-results/controls-dropdown-{theme}.png')
    option.click()
    expect(select).to_contain_text('18 px')
    expect(select).to_have_attribute('aria-expanded', 'false')
    select.click()
    page.keyboard.press('Escape')
    expect(select).to_have_attribute('aria-expanded', 'false')
    expect(settings).to_be_visible()
    settings.get_by_role('button', name='工作区', exact=True).click()
    field = settings.get_by_role('textbox', name='工作区路径', exact=True)
    field.focus()
    assert field.evaluate('el => getComputedStyle(el).outlineStyle') == 'none'
    assert field.evaluate('el => getComputedStyle(el).borderColor') == ('rgb(129, 133, 140)' if theme == 'light' else 'rgb(149, 153, 160)')
    page.screenshot(path=f'test-results/controls-workspace-{theme}.png')
