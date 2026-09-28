import pytest


def test_navigation_preserves_changes_and_theme(page):
    page.get_by_role("textbox", name="搜索记忆…").fill("Semi")
    page.get_by_role("link", name="知识库", exact=True).click()
    page.get_by_role("link", name="记忆", exact=True).click()
    assert page.get_by_role("textbox", name="搜索记忆…").input_value() == "Semi"
    page.keyboard.press("Control+k")
    page.get_by_role("textbox", name="搜索页面名称…").fill("Design")
    page.get_by_role("button", name="设计规范 / Design").click()
    assert page.get_by_role("heading", name="颜色与层级", exact=True).is_visible()
    assert page.locator("body").evaluate("el => getComputedStyle(el).getPropertyValue('--semi-color-primary').trim()") == "rgba(51,112,255,1)"
    assert page.locator(".sidebar").evaluate("el => getComputedStyle(el).position") == "fixed"
    assert page.locator(".shell-main").evaluate("el => getComputedStyle(el).overflowY") == "auto"


@pytest.mark.parametrize("width", [390, 768])
def test_narrow_layout_and_dialog(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    for name in ["记忆", "知识库", "设计规范"]:
        page.get_by_role("link", name=name, exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
    page.get_by_role("link", name="记忆", exact=True).click()
    page.get_by_role("button", name="添加记忆", exact=True).click()
    page.get_by_role("textbox", name="记忆内容", exact=True).wait_for()
    assert page.get_by_role("button", name="保存记忆", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
