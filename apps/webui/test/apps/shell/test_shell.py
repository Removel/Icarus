import re

import pytest


def test_navigation_preserves_changes_and_theme(page):
    page.get_by_role("textbox", name="搜索记忆…").fill("Semi")
    page.get_by_role("link", name="知识库", exact=True).click()
    page.get_by_role("tab", name=re.compile("^原始资料")).click()
    page.get_by_role("link", name="记忆", exact=True).click()
    assert page.get_by_role("textbox", name="搜索记忆…").input_value() == "Semi"
    page.get_by_role("link", name="知识库", exact=True).click()
    assert page.get_by_role("tab", name=re.compile("^原始资料")).get_attribute("aria-selected") == "true"
    page.keyboard.press("Control+k")
    page.get_by_role("textbox", name="搜索页面名称…").fill("Design")
    page.get_by_role("button", name=re.compile("设计规范")).click()
    assert page.get_by_role("heading", name="颜色与层级", exact=True).is_visible()
    assert page.locator("body").evaluate("el => getComputedStyle(el).getPropertyValue('--semi-color-primary').trim()") == "rgba(51,112,255,1)"
    assert page.locator(".app-dock").evaluate("el => getComputedStyle(el).position") == "fixed"
    assert page.locator(".shell-main").evaluate("el => getComputedStyle(el).overflowY") == "auto"


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_narrow_layout_and_dialog(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    for name in ["记忆", "知识库", "设计规范"]:
        page.get_by_role("link", name=name, exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
        assert page.locator(".shell-main").evaluate("el => el.scrollWidth <= el.clientWidth"), name
    page.get_by_role("link", name="记忆", exact=True).click()
    page.get_by_role("button", name="添加记忆", exact=True).click()
    page.get_by_role("textbox", name="记忆内容", exact=True).wait_for()
    assert page.get_by_role("button", name="保存记忆", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_shared_control_heights_and_optional_descriptions(page):
    assert page.locator(".page-heading-main > p:visible").count() == 0
    search_height = page.locator(".memory-page .search-field").bounding_box()["height"]
    button_height = page.get_by_role("button", name="添加记忆", exact=True).bounding_box()["height"]
    assert search_height == button_height == 36
    page.get_by_role("link", name="知识库", exact=True).click()
    select_height = page.get_by_role("combobox", name="筛选内容类型").bounding_box()["height"]
    assert select_height == 36
