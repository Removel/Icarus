import pytest
from playwright.sync_api import expect
from test.navigation import follow_link, open_navigation, open_settings


@pytest.fixture
def knowledge_service(page):
    def respond(route):
        path = route.request.url.split('/api/v1/')[-1]
        if path == 'kbs': payload = {'knowledge_bases': [{'name': '空知识库'}]}
        elif path == 'list': payload = {'documents': [], 'summaries': [], 'concepts': [], 'entities': []}
        elif path == 'graph': payload = {'nodes': [], 'edges': [], 'types': []}
        else: raise AssertionError(path)
        route.fulfill(json=payload)

    page.route('**/api/v1/**', respond)


def test_navigation_preserves_changes_and_theme(page, knowledge_service):
    page.get_by_role('textbox', name='搜索记忆…').fill('Semi')
    follow_link(page, '知识库')
    page.get_by_role('link', name='原始资料', exact=True).click()
    follow_link(page, '记忆')
    expect(page.get_by_role('textbox', name='搜索记忆…')).to_have_value('Semi')
    assert page.locator('body').evaluate("el => getComputedStyle(el).getPropertyValue('--semi-color-primary').trim()") == '#a45b70'
    expect(page.get_by_role('complementary', name='会话管理')).to_be_visible()
    expect(page.get_by_role('complementary', name='Icarus 导航')).to_have_count(0)


@pytest.mark.parametrize('width', [390, 768, 1280])
def test_narrow_layout_and_dialog(page, knowledge_service, width):
    page.set_viewport_size({'width': width, 'height': 844})
    for name in ['记忆', '知识库']:
        follow_link(page, name)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
        assert page.locator('.management-body:visible').evaluate('el => el.scrollWidth <= el.clientWidth'), name
    follow_link(page, '记忆')
    page.get_by_role('button', name='添加记忆', exact=True).click()
    page.get_by_role('textbox', name='记忆内容', exact=True).wait_for()
    expect(page.get_by_role('button', name='保存记忆', exact=True)).to_be_visible()


def test_shared_control_heights_and_optional_descriptions(page, knowledge_service):
    assert page.locator('.page-heading-main > p:visible').count() == 0
    assert page.locator('.memory-page .search-field').bounding_box()['height'] == 36
    assert page.get_by_role('button', name='添加记忆', exact=True).bounding_box()['height'] == 36
    follow_link(page, '知识库')
    assert page.get_by_role('combobox', name='筛选内容类型').bounding_box()['height'] == 36


def test_sidebar_collapse_preserves_page_state(page):
    page.get_by_role('textbox', name='搜索记忆…').fill('Semi')
    page.get_by_role('button', name='收起会话列表', exact=True).click()
    expect(page.get_by_role('complementary', name='会话管理')).not_to_be_visible()
    page.get_by_role('button', name='展开会话列表', exact=True).click()
    expect(page.get_by_role('complementary', name='会话管理')).to_be_visible()
    expect(page.get_by_role('textbox', name='搜索记忆…')).to_have_value('Semi')


@pytest.mark.parametrize('width', [390, 768, 1280])
def test_navigation_does_not_cover_content(page, width):
    page.set_viewport_size({'width': width, 'height': 844})
    expect(page.locator('.management-body:visible')).to_be_visible()
    header = page.locator('.management-header').bounding_box()
    content = page.locator('.management-body:visible').bounding_box()
    assert header['y'] + header['height'] <= content['y']
    if width >= 768:
        sidebar = page.get_by_role('complementary', name='会话管理').bounding_box()
        assert sidebar['x'] + sidebar['width'] <= content['x']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_design_reference_is_not_an_application_route(page):
    page.goto(page.url.split('#')[0] + '#/design')
    page.wait_for_url('**/#/chat')
    expect(page.get_by_role('heading', name='设计规范', exact=True)).to_have_count(0)


@pytest.mark.parametrize('width', [390, 1280])
def test_knowledge_categories_keep_existing_routes(page, knowledge_service, width):
    page.set_viewport_size({'width': width, 'height': 844})
    follow_link(page, '知识库')
    submenu = page.get_by_role('navigation', name='知识库分类')
    for label in ['原始资料', '知识页面', '文档关联', '质量检查']:
        expect(submenu.get_by_role('link', name=label, exact=True)).to_be_visible()
    submenu.get_by_role('link', name='知识页面', exact=True).click()
    expect(submenu.get_by_role('link', name='知识页面', exact=True)).to_have_attribute('aria-current', 'page')
    assert '#/knowledge/pages' in page.url
    follow_link(page, '记忆')
    follow_link(page, '知识库')
    assert '#/knowledge/pages' in page.url


def test_theme_follows_system_and_remembers_explicit_choice(page):
    page.emulate_media(color_scheme='dark')
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
    open_settings(page)
    page.get_by_role('button', name='浅色', exact=True).click()
    page.keyboard.press('Escape')
    expect(page.locator('html')).to_have_attribute('data-theme', 'light')
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-theme', 'light')


def test_mobile_navigation_traps_focus_and_preserves_page_state(page, knowledge_service):
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('textbox', name='搜索记忆…').fill('Semi')
    open_navigation(page)
    sidebar = page.get_by_role('complementary', name='会话管理')
    for _ in range(12):
        page.keyboard.press('Tab')
        assert sidebar.evaluate('el => el.contains(document.activeElement)')
    page.keyboard.press('Escape')
    expect(sidebar).not_to_be_visible()
    follow_link(page, '知识库')
    follow_link(page, '记忆')
    expect(page.get_by_role('textbox', name='搜索记忆…')).to_have_value('Semi')
