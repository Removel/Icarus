def test_create_edit_pause_restore_and_delete(page):
    page.get_by_role("button", name="添加记忆", exact=True).click()
    page.get_by_role("button", name="保存记忆", exact=True).click()
    assert page.get_by_role("alert").inner_text() == "请先填写记忆内容。"
    # Semi Modal suppresses repeated confirms within 100 ms; type at a human pace.
    page.get_by_role("textbox", name="记忆内容", exact=True).press_sequentially("浏览器验收：优先阅读原始资料", delay=15)
    page.get_by_role("button", name="保存记忆", exact=True).click()
    row = page.get_by_role("button", name="查看记忆：浏览器验收：优先阅读原始资料", exact=True)
    row.wait_for()
    assert row.is_visible()
    page.get_by_role("button", name="修正内容", exact=True).click()
    page.get_by_role("textbox", name="记忆内容", exact=True).fill("浏览器验收：重要结论保留引用")
    page.get_by_role("button", name="保存记忆", exact=True).click()
    page.get_by_role("button", name="暂停使用", exact=True).click()
    detail = page.get_by_role("complementary", name="记忆详情")
    assert detail.get_by_text("已停用", exact=True).is_visible()
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
    page.get_by_role("button", name="清除筛选", exact=True).click()
    assert page.get_by_role("button", name="查看记忆：", exact=False).count() == 8
