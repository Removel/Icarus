import re

import pytest
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
    detail = page.get_by_role("dialog")
    detail.get_by_text("已停用", exact=True).first.wait_for()
    assert detail.get_by_text("已停用", exact=True).first.is_visible()
    page.get_by_role("button", name="恢复使用", exact=True).click()
    detail.get_by_text("长期有效", exact=True).wait_for()
    assert detail.get_by_text("长期有效", exact=True).is_visible()
    page.get_by_role("button", name="删除记忆", exact=True).click()
    page.get_by_role("button", name="保留记忆", exact=True).click()
    assert detail.is_visible()
    page.get_by_role("button", name="删除记忆", exact=True).click()
    page.get_by_role("button", name="确认删除", exact=True).click()
    detail.wait_for(state='hidden')
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
    pick_option(page, "筛选用户", "chen")
    assert page.locator(".memory-entry").count() == 2
    pick_option(page, "筛选作用范围", "全局")
    assert page.locator(".memory-entry").count() == 1
    assert "先给出结论" in page.locator(".memory-entry").inner_text()
    page.get_by_role("button", name="清除筛选", exact=True).click()
    pick_option(page, "筛选记忆分类", "沟通习惯")
    group = page.locator(".memory-list")
    original = group.locator(".memory-content").all_text_contents()
    assert len(original) == 2
    page.get_by_role("button", name="更新时间：从新到旧", exact=True).click()
    assert group.locator(".memory-content").all_text_contents() == list(reversed(original))
    assert page.get_by_role("button", name="更新时间：从旧到新", exact=True).is_visible()


def test_bulk_lifecycle_and_selection_scope(page):
    page.get_by_role("button", name="多选", exact=True).click()
    select_all = page.get_by_role("checkbox", name="全选当前结果", exact=True)
    page.get_by_text("全选当前结果", exact=True).click()
    expect(page.locator(".selection-count")).to_have_text("已选 8 条")
    expect(page.get_by_role("button", name="恢复使用（2）", exact=True)).to_be_visible()
    page.get_by_role("button", name="暂停使用（6）", exact=True).click()
    expect(page.get_by_role("button", name="多选", exact=True)).to_be_focused()
    assert page.get_by_role("checkbox").count() == 0
    page.get_by_role("tab", name=re.compile("^生效中")).click()
    assert page.get_by_role("heading", name="没有找到匹配的内容").is_visible()
    page.get_by_role("tab", name=re.compile("^已失效")).click()
    assert page.locator(".memory-entry").count() == 8
    page.get_by_role("button", name="多选", exact=True).click()
    page.get_by_text("全选当前结果", exact=True).click()
    assert page.get_by_role("button", name=re.compile("^暂停使用")).count() == 0
    page.get_by_role("button", name="恢复使用（8）", exact=True).click()
    page.get_by_role("heading", name="没有找到匹配的内容").wait_for()
    assert page.get_by_role("heading", name="没有找到匹配的内容").is_visible()
    expect(page.get_by_role("region", name="记忆列表", exact=True)).to_be_focused()
    page.get_by_role("tab", name=re.compile("^生效中")).click()
    assert page.locator(".memory-entry").count() == 8


def test_bulk_rows_keyboard_and_filtered_results(page):
    search = page.get_by_role("textbox", name="搜索记忆…")
    search.fill("中文")
    page.get_by_role("button", name="多选", exact=True).click()
    select_all = page.get_by_role("checkbox", name="全选当前结果", exact=True)
    expect(select_all).to_be_focused()
    row = page.get_by_role("checkbox", name=re.compile("^选择记忆："))
    page.locator(".memory-content").click()
    expect(row).to_be_checked()
    assert page.get_by_role("dialog").count() == 0
    expect(page.get_by_role("button", name="暂停使用（1）", exact=True)).to_be_visible()
    row.press("Space")
    expect(row).not_to_be_checked()
    page.get_by_text("全选当前结果", exact=True).click()
    select_all.press("Escape")
    expect(page.get_by_role("button", name="多选", exact=True)).to_be_focused()
    expect(search).to_have_value("中文")
    page.get_by_role("button", name="多选", exact=True).click()
    page.get_by_text("全选当前结果", exact=True).click()
    page.get_by_role("button", name="暂停使用（1）", exact=True).click()
    expect(search).to_have_value("中文")
    expect(page.locator(".memory-entry .status")).to_have_text("已停用")
    page.get_by_role("button", name="清除筛选", exact=True).click()
    page.get_by_role("tab", name=re.compile("^生效中")).click()
    assert page.locator(".memory-entry").count() == 5


@pytest.mark.parametrize("change", ["user", "scope", "tab", "navigation"])
def test_bulk_selection_is_cleared_when_context_changes(page, change):
    page.get_by_role("button", name="多选", exact=True).click()
    select_all = page.get_by_role("checkbox", name="全选当前结果", exact=True)
    page.get_by_text("全选当前结果", exact=True).click()
    if change == "user":
        pick_option(page, "筛选用户", "chen")
    elif change == "scope":
        pick_option(page, "筛选作用范围", "全局")
    elif change == "tab":
        page.get_by_role("tab", name=re.compile("^已失效")).click()
    else:
        page.get_by_role("link", name="知识库", exact=True).click()
        page.get_by_role("link", name="记忆", exact=True).click()
    expect(page.get_by_role("button", name="多选", exact=True)).to_be_visible()
    page.get_by_role("button", name="多选", exact=True).click()
    expect(select_all).not_to_be_checked()
    expect(page.locator(".selection-count")).to_have_text("已选 0 条")
    assert page.get_by_role("button", name=re.compile("^(暂停|恢复)使用")).count() == 0
    page.get_by_role("button", name="取消多选", exact=True).click()
    page.locator(".memory-entry-open").first.click()
    expect(page.get_by_role("dialog")).to_be_visible()


def test_modal_cancel_and_escape_keep_context(page):
    original = page.locator(".memory-content").first.inner_text()
    page.get_by_role("button", name=f"查看记忆：{original}", exact=True).click()
    detail = page.get_by_role("dialog")
    assert not detail.locator(".memory-history").evaluate("element => element.open")
    detail.locator(".memory-history > summary").click()
    assert detail.locator(".memory-timeline").is_visible()
    page.get_by_role("button", name="修正内容", exact=True).click()
    editor = page.get_by_role("textbox", name="修正记忆内容")
    editor.fill("不应该保存的修改")
    page.get_by_role("button", name="取消修改", exact=True).click()
    assert page.get_by_role("dialog").count() == 1
    page.get_by_role("button", name="继续编辑").click()
    assert editor.input_value() == "不应该保存的修改"
    page.get_by_role("button", name="取消修改", exact=True).click()
    page.get_by_role("button", name="放弃修改").click()
    assert detail.locator(".memory-detail-text").inner_text() == original
    assert page.get_by_role("textbox", name="修正记忆内容").count() == 0
    page.get_by_role("button", name="删除记忆", exact=True).click()
    page.keyboard.press("Control+k")
    assert page.get_by_role("textbox", name="搜索页面名称…").count() == 0
    page.keyboard.press("Escape")
    assert detail.locator(".memory-detail-text").is_visible()
    page.keyboard.press("Escape")
    detail.wait_for(state="hidden")


def test_modal_deep_link_history_and_reload(page):
    entries = page.locator(".memory-content")
    first = entries.first.inner_text()
    page.get_by_role("button", name=f"查看记忆：{first}", exact=True).click()
    detail = page.get_by_role("dialog")
    page.go_back()
    detail.wait_for(state="hidden")
    page.go_forward()
    page.wait_for_function("location.hash.includes('entry=mem_a8f2c1')")
    assert detail.locator(".memory-detail-text").inner_text() == first
    page.reload()
    detail.wait_for()
    assert detail.locator(".memory-detail-text").inner_text() == first
    page.evaluate("location.hash = '/memory/all?entry=missing'")
    page.wait_for_function("!location.hash.includes('entry=')")
    detail.wait_for(state="hidden")


def test_card_grid_global_sort_and_return_context(page):
    entries = page.locator(".memory-content")
    original = entries.all_text_contents()
    assert page.locator(".memory-group").count() == 0
    cards = page.locator('.memory-entry')
    first, second = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
    assert first['y'] == second['y']
    assert second['x'] > first['x']
    assert first['height'] < 220
    page.get_by_role("button", name="更新时间：从新到旧", exact=True).click()
    assert entries.all_text_contents() == list(reversed(original))
    page.set_viewport_size({"width": 1280, "height": 480})
    entry = page.locator(".memory-entry-open").last
    entry.scroll_into_view_if_needed()
    position = page.locator(".shell-main").evaluate("element => element.scrollTop")
    assert position > 0
    entry.click()
    page.get_by_role("dialog").wait_for()
    page.keyboard.press("Escape")
    page.get_by_role("dialog").wait_for(state="hidden")
    expect(entry).to_be_focused()
    assert abs(page.locator(".shell-main").evaluate("element => element.scrollTop") - position) <= 1


def test_unsaved_escape_and_browser_back_require_confirmation(page):
    page.locator(".memory-entry-open").first.click()
    detail = page.get_by_role("dialog")
    detail.get_by_role("button", name="修正内容", exact=True).click()
    editor = detail.get_by_role("textbox", name="修正记忆内容")
    editor.fill("未保存草稿")
    page.keyboard.press("Escape")
    detail.get_by_role("button", name="继续编辑").click()
    assert editor.input_value() == "未保存草稿"
    page.go_back()
    detail.get_by_role("heading", name="放弃未保存的修改？").wait_for()
    assert page.get_by_role("dialog").count() == 1
    assert "entry=" in page.url
    detail.get_by_role("button", name="继续编辑").click()
    expect(editor).to_have_value("未保存草稿")
    page.go_back()
    detail.get_by_role("button", name="放弃修改").click()
    detail.wait_for(state="hidden")
    assert "entry=" not in page.url
    assert "未保存草稿" not in page.locator(".memory-list").inner_text()
    page.go_forward()
    expect(detail.locator('.memory-detail-text')).to_be_visible()
    assert '未保存草稿' not in detail.locator('.memory-detail-text').inner_text()


def test_mask_close_preserves_search_and_keyboard_save(page):
    search = page.get_by_role("textbox", name="搜索记忆…")
    search.fill("中文")
    page.locator(".memory-entry-open").click()
    detail = page.get_by_role("dialog")
    detail.get_by_role("button", name="修正内容", exact=True).click()
    editor = detail.get_by_role("textbox", name="修正记忆内容")
    editor.fill(" ")
    detail.get_by_role("button", name="保存修改", exact=True).click()
    assert detail.get_by_role("alert").inner_text() == "请先填写记忆内容。"
    editor.fill("中文：键盘保存后的记忆")
    editor.press("Control+Enter")
    expect(detail.locator(".memory-detail-text")).to_have_text("中文：键盘保存后的记忆")
    page.locator(".semi-modal-wrap").click(position={"x": 5, "y": 5})
    detail.wait_for(state="hidden")
    assert search.input_value() == "中文"
    assert page.locator(".memory-entry").count() == 1


@pytest.mark.parametrize("width", [390, 768])
def test_narrow_detail_and_editor_remain_usable(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.locator(".memory-entry-open").first.click()
    detail = page.get_by_role("dialog")
    assert detail.bounding_box()["width"] <= width
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    detail.get_by_role("button", name="修正内容", exact=True).click()
    detail.get_by_role("textbox", name="修正记忆内容").fill("手机修正后的记忆")
    detail.get_by_role("button", name="保存修改", exact=True).click()
    assert detail.locator(".memory-detail-text").inner_text() == "手机修正后的记忆"
    assert detail.get_by_role("button", name="修正内容", exact=True).is_visible()
    page.keyboard.press("Escape")
    detail.wait_for(state="hidden")
    assert page.locator(".memory-entry-open").first.is_visible()


@pytest.mark.parametrize('departure', ['escape', 'mask', 'cancel', 'navigation'])
def test_create_draft_is_protected_and_can_be_saved(page, departure):
    page.get_by_role('button', name='添加记忆', exact=True).click()
    editor = page.get_by_role('textbox', name='记忆内容', exact=True)
    editor.fill('新增流程的未保存内容')
    if departure == 'escape':
        page.keyboard.press('Escape')
    elif departure == 'mask':
        page.locator('.semi-modal-wrap').click(position={'x': 5, 'y': 5})
    elif departure == 'cancel':
        page.get_by_role('button', name='取消', exact=True).click()
    else:
        page.evaluate("location.hash = '/knowledge/pages'")
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('heading', name='放弃未保存的修改？').wait_for()
    assert page.get_by_role('dialog').count() == 1
    dialog.get_by_role('button', name='继续编辑').click()
    expect(editor).to_have_value('新增流程的未保存内容')
    editor.press('Control+Enter')
    expect(page.locator('.memory-detail-text')).to_have_text('新增流程的未保存内容')


def test_create_discard_clears_only_the_new_draft(page):
    page.get_by_role('button', name='添加记忆', exact=True).click()
    page.get_by_role('textbox', name='记忆内容', exact=True).fill('changed-draft')
    page.keyboard.press('Escape')
    page.get_by_role('button', name='放弃修改', exact=True).click()
    page.get_by_role('dialog').wait_for(state='hidden')
    page.get_by_role('button', name='添加记忆', exact=True).click()
    expect(page.get_by_role('textbox', name='记忆内容', exact=True)).to_have_value('')
    page.keyboard.press('Escape')
    page.get_by_role('dialog').wait_for(state='hidden')
    assert page.locator('.memory-entry').count() == 8


def test_clear_filters_preserves_status_tab_and_individual_filters(page):
    tab = page.get_by_role('tab', name=re.compile('^生效中'))
    tab.click()
    pick_option(page, '筛选用户', 'chen')
    page.get_by_role('textbox', name='搜索记忆…').fill('不存在')
    page.get_by_role('button', name='移除搜索条件', exact=True).click()
    expect(page.get_by_role('combobox', name='筛选用户')).to_contain_text('chen')
    page.get_by_role('button', name='清除筛选', exact=True).click()
    expect(tab).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_role('combobox', name='筛选用户')).to_contain_text('所有用户')


@pytest.mark.parametrize('width', [390, 1440])
def test_memory_content_appears_early_in_viewport(page, width):
    page.set_viewport_size({'width': width, 'height': 844})
    first = page.locator('.memory-entry').first.bounding_box()
    assert first['y'] < (440 if width == 390 else 370)
    assert page.locator('.memory-meta').first.evaluate('el => parseFloat(getComputedStyle(el).fontSize)') >= 12
