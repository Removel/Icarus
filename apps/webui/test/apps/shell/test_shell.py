import pytest


def wait_for_sidebar_width(page, width):
    page.wait_for_function(
        "width => document.querySelector('.app-sidebar').getBoundingClientRect().width === width",
        arg=width,
    )


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
    page.get_by_role("textbox", name="搜索记忆…").fill("Semi")
    page.get_by_role("link", name="知识库", exact=True).click()
    page.get_by_role("link", name="原始资料", exact=True).click()
    page.get_by_role("link", name="记忆", exact=True).click()
    assert page.get_by_role("textbox", name="搜索记忆…").input_value() == "Semi"
    page.get_by_role("link", name="知识库", exact=True).click()
    assert page.get_by_role("link", name="原始资料", exact=True).get_attribute("aria-current") == "page"
    assert page.get_by_role("link", name="设计规范", exact=True).count() == 0
    assert page.locator("body").evaluate("el => getComputedStyle(el).getPropertyValue('--semi-color-primary').trim()") == "rgba(51,112,255,1)"
    assert page.locator(".app-dock").count() == 0
    assert page.locator(".app-layout.semi-layout").count() == 1
    assert page.get_by_role("banner").is_visible()
    assert page.get_by_role("complementary", name="Icarus 导航").is_visible()
    assert page.locator(".shell-main").evaluate("el => getComputedStyle(el).overflowY") == "auto"


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_narrow_layout_and_dialog(page, knowledge_service, width):
    page.set_viewport_size({"width": width, "height": 844})
    for name in ["记忆", "知识库"]:
        page.get_by_role("link", name=name, exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
        assert page.locator(".shell-main").evaluate("el => el.scrollWidth <= el.clientWidth"), name
    page.get_by_role("link", name="记忆", exact=True).click()
    page.get_by_role("button", name="添加记忆", exact=True).click()
    page.get_by_role("textbox", name="记忆内容", exact=True).wait_for()
    assert page.get_by_role("button", name="保存记忆", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_shared_control_heights_and_optional_descriptions(page, knowledge_service):
    assert page.locator(".page-heading-main > p:visible").count() == 0
    search_height = page.locator(".memory-page .search-field").bounding_box()["height"]
    button_height = page.get_by_role("button", name="添加记忆", exact=True).bounding_box()["height"]
    assert search_height == button_height == 36
    page.get_by_role("link", name="知识库", exact=True).click()
    select_height = page.get_by_role("combobox", name="筛选内容类型").bounding_box()["height"]
    assert select_height == 36


def test_sidebar_collapse_preserves_page_state(page):
    page.get_by_role("textbox", name="搜索记忆…").fill("Semi")
    expanded_width = page.locator(".app-sidebar").bounding_box()["width"]
    page.get_by_role("button", name="收起侧边栏", exact=True).click()
    wait_for_sidebar_width(page, 64)
    assert page.locator(".app-sidebar").bounding_box()["width"] == 64
    assert page.get_by_role("link", name="记忆", exact=True).is_visible()
    assert page.get_by_role("textbox", name="搜索记忆…").input_value() == "Semi"
    # Semi debounces repeated clicks on the same control for 100 ms.
    page.wait_for_timeout(120)
    page.get_by_role("button", name="展开侧边栏", exact=True).click()
    wait_for_sidebar_width(page, expanded_width)
    assert page.locator(".app-sidebar").bounding_box()["width"] == expanded_width


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_navigation_does_not_cover_content(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    # Chromium can apply dynamic viewport units after the resize command returns.
    page.wait_for_function(
        "document.querySelector('.app-layout').getBoundingClientRect().height === 844"
    )
    header = page.get_by_role("banner").bounding_box()
    sidebar = page.locator(".app-sidebar").bounding_box()
    content = page.locator(".shell-main").bounding_box()
    assert header["y"] + header["height"] <= sidebar["y"]
    if width < 768:
        assert sidebar["y"] + sidebar["height"] <= content["y"]
    else:
        assert sidebar["x"] + sidebar["width"] <= content["x"]
    page.locator(".shell-main").evaluate("element => element.scrollTop = element.scrollHeight")
    assert page.get_by_role("banner").bounding_box() == header
    assert page.locator(".app-sidebar").bounding_box() == sidebar


def test_design_reference_is_not_an_application_route(page):
    page.goto(page.url.split('#')[0] + '#/design')
    page.wait_for_url('**/#/memory/all')
    assert page.get_by_role('heading', name='设计规范', exact=True).count() == 0


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_knowledge_subnavigation_belongs_to_knowledge(page, knowledge_service, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.get_by_role("link", name="知识库", exact=True).click()
    submenu = page.get_by_role("navigation", name="知识库分类")
    assert page.locator(".nav-group-knowledge").get_by_role("navigation", name="知识库分类").is_visible()
    knowledge = page.get_by_role("link", name="知识库", exact=True).bounding_box()
    chat = page.get_by_role("link", name="对话", exact=True).bounding_box()
    box = submenu.bounding_box()
    if width >= 768:
        assert knowledge["y"] + knowledge["height"] <= box["y"]
        assert box["y"] + box["height"] <= chat["y"]
    else:
        assert knowledge["y"] == chat["y"]
        assert box["y"] >= chat["y"] + chat["height"]
        assert box["y"] + box["height"] <= page.locator(".shell-main").bounding_box()["y"]
    for label in ["原始资料", "知识页面", "文档关联", "质量检查"]:
        assert submenu.get_by_role("link", name=label, exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if width == 1280:
        page.get_by_role("button", name="收起侧边栏", exact=True).click()
        wait_for_sidebar_width(page, 64)
        assert page.locator(".app-sidebar").bounding_box()["width"] == 64
        box = submenu.bounding_box()
        assert box["y"] + box["height"] <= page.get_by_role("link", name="对话", exact=True).bounding_box()["y"]
    page.get_by_role("link", name="记忆", exact=True).click()
    assert submenu.is_visible()
    assert submenu.locator('[aria-current=page]').count() == 0
