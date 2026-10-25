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


def test_filter_popup_closes_on_browser_navigation(page):
    follow_link(page, '对话')
    follow_link(page, '记忆')
    select = page.get_by_role('combobox', name='筛选用户', exact=True)
    select.click()
    expect(page.get_by_role('listbox')).to_be_visible()
    page.go_back()
    expect(page.get_by_role('textbox', name='发送消息')).to_be_visible()
    expect(page.get_by_role('listbox')).to_have_count(0)
    expect(page.locator('.icarus-select-mask')).to_have_count(0)
    page.go_forward()
    expect(select).to_be_visible()
    expect(select).to_have_attribute('aria-expanded', 'false')


@pytest.mark.parametrize('width', [390, 768, 1280])
def test_narrow_layout_and_dialog(page, knowledge_service, width):
    page.set_viewport_size({'width': width, 'height': 844})
    for name in ['记忆', '知识库']:
        follow_link(page, name)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
        assert page.locator('.management-body:visible').evaluate('el => el.scrollWidth <= el.clientWidth'), name
        header = page.locator('.management-header').bounding_box()
        navigation = page.get_by_role('tablist', name='记忆状态') if name == '记忆' else page.get_by_role('navigation', name='知识库分类')
        box = navigation.bounding_box()
        assert abs((box['x'] + box['width'] / 2) - (header['x'] + header['width'] / 2)) <= 1
        assert page.locator('.management-title').bounding_box()['height'] <= 24
        if width > 1100:
            back = page.get_by_role('link', name='返回对话', exact=True).bounding_box()
            assert box['y'] + box['height'] / 2 > back['y'] + back['height'] / 2
        page.screenshot(path=f'test-results/dswui-management-{name}-{width}.png')
    follow_link(page, '记忆')
    page.get_by_role('button', name='添加记忆', exact=True).click()
    page.get_by_role('textbox', name='记忆内容', exact=True).wait_for()
    expect(page.get_by_role('button', name='保存记忆', exact=True)).to_be_visible()


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_shared_control_heights_and_optional_descriptions(page, knowledge_service, theme):
    page.emulate_media(color_scheme=theme)
    assert page.locator('.page-heading-main > p:visible').count() == 0
    assert page.locator('.memory-page .search-field').bounding_box()['height'] == 36
    assert page.get_by_role('button', name='添加记忆', exact=True).bounding_box()['height'] == 36
    filters = [page.locator('.memory-filters .search-field'), *[
        page.get_by_role('combobox', name=label) for label in ['筛选记忆分类', '筛选用户', '筛选作用范围']
    ]]
    boxes = [control.bounding_box() for control in filters]
    assert len({box['y'] for box in boxes}) == 1
    assert all(left['x'] + left['width'] <= right['x'] for left, right in zip(boxes, boxes[1:]))
    page.screenshot(path=f'test-results/dswui-memory-{theme}.png')
    active = page.get_by_role('tab', name='全部 8', exact=True)
    active.focus()
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='生效中 6', exact=True)).to_have_attribute('aria-selected', 'true')
    follow_link(page, '知识库')
    assert page.get_by_role('combobox', name='筛选内容类型').bounding_box()['height'] == 36
    page.screenshot(path=f'test-results/dswui-knowledge-{theme}.png')
    follow_link(page, '记忆')
    page.get_by_role('button', name='添加记忆', exact=True).click()
    modal = page.get_by_role('dialog', name='添加记忆', exact=True)
    scope = modal.get_by_role('combobox', name='记忆作用范围', exact=True)
    scope.press('ArrowDown')
    page.keyboard.press('End')
    page.keyboard.press('Enter')
    expect(modal.get_by_role('textbox', name='记忆工作区路径', exact=True)).to_be_visible()
    scope.click()
    page.screenshot(path=f'test-results/dswui-memory-select-{theme}.png')
    page.keyboard.press('Escape')
    expect(page.get_by_role('listbox')).to_have_count(0)
    expect(modal).to_be_visible()
    expect(scope).to_be_focused()


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
