"""Independent chat layout and settings navigation, using existing RPC fixtures."""
import pytest
from playwright.sync_api import expect

from test.packages.chat_app.test_chat import gateway
from test.navigation import open_settings, follow_link


@pytest.fixture
def page(app_page):
    return app_page


@pytest.mark.parametrize('suffix', ['', '#/unknown'])
def test_chat_is_default_without_workbench_chrome(page, suffix):
    page.goto('/' + suffix)
    expect(page.get_by_role('region', name='Agent 对话')).to_be_visible()
    expect(page.get_by_role('banner')).not_to_be_visible()
    expect(page.get_by_role('complementary', name='Icarus 导航')).not_to_be_visible()
    expect(page.get_by_role('button', name='账户菜单', exact=True)).to_be_visible()
    assert page.locator('.chat-sessions:visible').count() == 1


@pytest.mark.parametrize('width', [390, 768, 1280])
def test_settings_navigation_returns_to_same_chat_and_preserves_management(page, width):
    state, _ = gateway(page)
    page.set_viewport_size({'width': width, 'height': 900})
    open_settings(page)
    settings = page.get_by_role('dialog', name='设置', exact=True)
    expect(settings).to_be_visible()
    page.keyboard.press('Escape')
    follow_link(page, '记忆')
    expect(settings).not_to_be_visible()
    expect(page.get_by_role('heading', name='记忆', exact=True)).to_be_visible()
    page.get_by_role('textbox', name='搜索记忆…').fill('Semi')
    page.get_by_role('link', name='返回对话', exact=True).click()
    expect(page.get_by_role('region', name='Agent 对话')).to_be_visible()
    assert 'session=session-1' in page.url
    assert len(state['sockets']) == 1
    assert sum(c['method'] == 'session.subscribe' for c in state['calls']) == 1
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_settings_keep_draft_theme_and_close_before_leave_guard(page):
    gateway(page)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('设置不能清空草稿')
    trigger = page.get_by_role('button', name='账户菜单', exact=True)
    open_settings(page)
    settings = page.get_by_role('dialog', name='设置', exact=True)
    settings.get_by_role('button', name='深色', exact=True).click()
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
    page.keyboard.press('Escape')
    expect(settings).not_to_be_visible()
    expect(trigger).to_be_focused()
    expect(editor).to_have_value('设置不能清空草稿')
    open_settings(page)
    page.keyboard.press('Escape')
    follow_link(page, '记忆')
    expect(settings).not_to_be_visible()
    guard = page.get_by_role('dialog', name='放弃未发送的消息？', exact=True)
    expect(guard).to_be_visible()
    guard.get_by_role('button', name='继续编辑', exact=True).click()
    expect(editor).to_have_value('设置不能清空草稿')
    follow_link(page, '记忆')
    guard.get_by_role('button', name='放弃', exact=True).click()
    expect(page.get_by_role('heading', name='记忆', exact=True)).to_be_visible()
    page.get_by_role('link', name='返回对话', exact=True).click()
    expect(editor).to_have_value('')
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')


def test_chat_sidebar_collapses_without_changing_management_preference(page):
    gateway(page)
    page.get_by_role('button', name='收起会话列表', exact=True).click()
    expect(page.get_by_role('complementary', name='会话管理')).not_to_be_visible()
    follow_link(page, '记忆')
    assert page.locator('.chat-sessions').bounding_box()['width'] == 261
    page.get_by_role('link', name='返回对话', exact=True).click()
    # Opening settings uses the original collapsed toolbar's expand action.
    expect(page.get_by_role('complementary', name='会话管理')).to_be_visible()
    page.get_by_role('button', name='收起会话列表', exact=True).click()
    page.get_by_role('button', name='展开会话列表', exact=True).click()
    expect(page.get_by_role('complementary', name='会话管理')).to_be_visible()


@pytest.mark.parametrize('theme,canvas,ink', [
    ('light', 'rgb(255, 255, 255)', 'rgb(15, 17, 21)'),
    ('dark', 'rgb(21, 21, 23)', 'rgb(250, 248, 249)'),
])
def test_loading_chat_styles_does_not_restyle_memory(memory_page, theme, canvas, ink):
    page = memory_page
    page.emulate_media(color_scheme=theme)
    # Media-query changes arrive asynchronously; compare the same applied theme
    # before and after crossing lazy module boundaries. The attribute flips
    # before the computed style settles, so wait for the painted theme itself.
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    expect(page.locator('.memory-page')).to_have_css('background-color', canvas)
    expect(page.locator('.memory-page')).to_have_css('color', ink)
    def appearance():
        return page.locator('.memory-page').evaluate('''el => {
            const style = getComputedStyle(el);
            const entry = el.querySelector('.memory-entry');
            const card = getComputedStyle(entry);
            return [style.backgroundColor, style.color, style.fontFamily, style.fontSize,
                    card.backgroundColor, card.borderRadius, card.padding, entry.getBoundingClientRect().width];
        }''')
    before = appearance()
    follow_link(page, '对话')
    follow_link(page, '记忆')
    assert appearance() == before
    page.screenshot(path=f'test-results/dswui-integration/b-memory-after-chat-{theme}.png')
