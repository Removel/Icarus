import re

from playwright.sync_api import expect


def test_create_edit_pause_restore_and_delete(page):
    page.get_by_role("button", name="添加记忆", exact=True).click()
    page.get_by_role("button", name="保存记忆", exact=True).click()
    assert page.get_by_role("alert").inner_text() == "请先填写记忆内容。"
    page.get_by_role("textbox", name="记忆内容", exact=True).press_sequentially("浏览器验收：优先阅读原始资料", delay=15)
    page.get_by_role("button", name="保存记忆", exact=True).click()
    row = page.get_by_role("button", name="查看记忆：浏览器验收：优先阅读原始资料", exact=True)
    row.wait_for()
    assert row.is_visible()
    page.get_by_role("button", name="修正内容", exact=True).click()
    page.get_by_role("textbox", name="修正记忆内容", exact=True).fill("浏览器验收：重要结论保留引用")
    page.get_by_role("button", name="保存修改", exact=True).click()
    page.get_by_role("button", name="暂停使用", exact=True).click()
    detail = page.get_by_role("complementary", name="记忆详情")
    assert detail.get_by_text("已停用", exact=True).first.is_visible()
    page.get_by_role("button", name="恢复使用", exact=True).click()
    assert detail.get_by_text("长期有效", exact=True).is_visible()
    page.get_by_role("button", name="删除这条记忆", exact=True).click()
    page.get_by_role("button", name="保留记忆", exact=True).click()
    assert detail.is_visible()
    page.get_by_role("button", name="删除这条记忆", exact=True).click()
    page.get_by_role("button", name="确认删除", exact=True).click()
    assert page.get_by_role("button", name="查看记忆：浏览器验收：重要结论保留引用", exact=True).count() == 0


def test_filter_empty_and_reset(page):
    page.get_by_role("textbox", name="搜索记忆…").fill("没有这个内容_xyz")
    assert page.get_by_role("heading", name="没有找到匹配的内容").is_visible()
    page.get_by_role("button", name="清除筛选", exact=True).last.click()
    assert page.get_by_role("button", name="查看记忆：", exact=False).count() == 8


def pick_option(page, combobox_label, option_text):
    combobox = page.get_by_role("combobox", name=combobox_label)
    combobox.click()
    option = page.locator(".semi-select-option").filter(has_text=re.compile(f"^{re.escape(option_text)}$"))
    option.wait_for(state="visible")
    option.click()
    # 面板关闭与列表重渲染有一拍延迟，先等所选值落到控件上再返回。
    page.locator(".semi-select-option:visible").first.wait_for(state="detached")
    expect(combobox).to_contain_text(option_text)


def test_scope_user_category_and_sort(page):
    page.get_by_role("button", name="筛选", exact=True).click()
    pick_option(page, "筛选用户", "chen")
    assert page.locator(".memory-entry").count() == 2
    pick_option(page, "筛选作用范围", "全局")
    assert page.locator(".memory-entry").count() == 1
    assert "先给出结论" in page.locator(".memory-entry").inner_text()
    page.get_by_role("button", name="清除筛选", exact=True).click()
    pick_option(page, "筛选记忆分类", "沟通习惯")
    group = page.locator(".memory-group", has=page.locator("h2", has_text="沟通习惯"))
    original = group.locator(".memory-content").all_text_contents()
    assert len(original) == 2
    page.get_by_role("button", name="更新时间：从新到旧", exact=True).click()
    assert group.locator(".memory-content").all_text_contents() == list(reversed(original))
    assert page.get_by_role("button", name="更新时间：从旧到新", exact=True).is_visible()


def test_bulk_lifecycle_and_selection_scope(page):
    page.get_by_role("button", name="多选", exact=True).click()
    select_all = page.locator(".selection-toolbar .semi-checkbox")
    select_all.click()
    page.get_by_role("textbox", name="搜索记忆…").fill("中文")
    assert "semi-checkbox-checked" not in (select_all.get_attribute("class") or "")
    assert page.get_by_role("button", name="暂停所选", exact=True).is_disabled()
    page.get_by_role("textbox", name="搜索记忆…").fill("")
    select_all.click()
    page.get_by_role("button", name="暂停所选", exact=True).click()
    assert page.get_by_role("button", name="暂停所选", exact=True).is_disabled()
    page.get_by_role("tab", name=re.compile("^生效中")).click()
    assert page.get_by_role("heading", name="没有找到匹配的内容").is_visible()
    page.get_by_role("tab", name=re.compile("^已失效")).click()
    assert page.locator(".memory-entry").count() == 8
    select_all.click()
    page.get_by_role("button", name="恢复所选", exact=True).click()
    assert page.get_by_role("heading", name="没有找到匹配的内容").is_visible()
    page.get_by_role("tab", name=re.compile("^生效中")).click()
    assert page.locator(".memory-entry").count() == 8


def test_inline_cancel_and_modal_escape_keep_context(page):
    original = page.locator(".memory-content").first.inner_text()
    page.get_by_role("button", name=f"修正记忆：{original}", exact=True).click()
    editor = page.get_by_role("textbox", name="修正记忆内容")
    editor.fill("不应该保存的修改")
    editor.press("Escape")
    assert page.get_by_role("button", name=f"查看记忆：{original}", exact=True).is_visible()
    assert page.get_by_role("textbox", name="修正记忆内容").count() == 0
    page.get_by_role("button", name=f"查看记忆：{original}", exact=True).click()
    page.get_by_role("button", name="删除这条记忆", exact=True).click()
    page.keyboard.press("Control+k")
    assert page.get_by_role("textbox", name="搜索页面名称…").count() == 0
    page.keyboard.press("Escape")
    page.get_by_role("dialog").wait_for(state="hidden")
    assert page.get_by_role("complementary", name="记忆详情").is_visible()


def test_nonmodal_selection_history_and_reload(page):
    entries = page.locator(".memory-content")
    first, second = entries.nth(0).inner_text(), entries.nth(1).inner_text()
    page.get_by_role("button", name=f"查看记忆：{first}", exact=True).click()
    page.get_by_role("button", name=f"查看记忆：{second}", exact=True).click()
    detail = page.get_by_role("complementary", name="记忆详情")
    assert detail.locator(".inspector-memory").inner_text() == second
    page.go_back()
    page.wait_for_function("location.hash.includes('entry=mem_a8f2c1')")
    assert detail.locator(".inspector-memory").inner_text() == first
    page.reload()
    detail.wait_for()
    assert detail.locator(".inspector-memory").inner_text() == first
    page.evaluate("location.hash = '/memory/all?entry=missing'")
    page.wait_for_function("!location.hash.includes('entry=')")
    assert page.get_by_role("complementary", name="记忆详情").count() == 0
