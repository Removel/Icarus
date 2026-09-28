import re

from playwright.sync_api import expect


def open_knowledge(page):
    page.get_by_role("link", name="知识库", exact=True).click()


def pick_option(page, combobox_label, option_text):
    combobox = page.get_by_role("combobox", name=combobox_label)
    combobox.click()
    option = page.locator(".semi-select-option").filter(has_text=re.compile(f"^{re.escape(option_text)}$"))
    option.wait_for(state="visible")
    option.click()
    # 面板关闭与列表重渲染有一拍延迟，先等所选值落到控件上再返回。
    page.locator(".semi-select-option:visible").first.wait_for(state="detached")
    expect(combobox).to_contain_text(option_text)


def test_import_retry_and_source_deletion(page):
    open_knowledge(page)
    page.get_by_role("tab", name=re.compile("^原始资料")).click()
    page.get_by_role("button", name="查看资料：早期讨论记录.pdf", exact=True).click()
    page.get_by_role("button", name="重新编译", exact=True).click()
    detail = page.get_by_role("region", name="资料详情", exact=True)
    detail.get_by_text("已就绪", exact=True).wait_for()
    assert detail.get_by_text("已就绪", exact=True).is_visible()
    page.get_by_role("button", name="返回资料列表", exact=True).click()
    page.get_by_role("button", name="导入资料", exact=True).click()
    page.get_by_role("button", name="使用示例文件", exact=True).click()
    page.get_by_role("button", name="开始导入", exact=True).click()
    page.get_by_role("heading", name="正文尚未解析", exact=True).wait_for()
    assert page.get_by_role("heading", name="正文尚未解析", exact=True).is_visible()
    assert detail.get_by_text("未解析", exact=True).is_visible()
    page.get_by_role("button", name="移除资料", exact=True).click()
    page.get_by_role("button", name="保留资料", exact=True).click()
    assert detail.is_visible()
    page.get_by_role("button", name="移除资料", exact=True).click()
    page.get_by_role("button", name="确认移除", exact=True).click()
    page.get_by_role("region", name="资料列表", exact=True).wait_for()
    assert page.get_by_role("button", name="查看资料：协作与设计原则.md", exact=True).count() == 0
    page.get_by_role("tab", name=re.compile("^知识页面")).click()
    assert page.locator(".knowledge-card").count() == 6


def test_page_edit_graph_and_quality(page):
    open_knowledge(page)
    page.get_by_role("button", name="阅读知识：Agent 分层架构", exact=True).click()
    page.get_by_role("button", name="编辑页面", exact=True).click()
    page.get_by_role("textbox", name="知识页面正文").fill("## 验收记录\n重要结论保留来源。")
    page.get_by_role("button", name="保存页面", exact=True).click()
    assert page.get_by_role("heading", name="验收记录", exact=True).is_visible()
    page.get_by_role("button", name="返回知识页面列表", exact=True).click()
    assert page.get_by_role("button", name="阅读知识：Agent 分层架构", exact=True).get_by_text("重要结论保留来源。", exact=True).is_visible()
    page.get_by_role("tab", name="来源关联", exact=True).click()
    assert page.locator(".relation-item").count() == 4
    assert page.locator(".isolated-pages button").count() == 2
    node = page.locator(".relation-map").get_by_role("button", name=re.compile("^插件运行时"))
    node.focus()
    page.keyboard.press("Enter")
    assert page.get_by_role("region", name="知识页面详情").get_by_role("heading", name="插件运行时", exact=True).is_visible()
    page.get_by_role("button", name="返回来源关联", exact=True).click()
    page.get_by_role("button", name="质量检查", exact=True).click()
    page.get_by_role("button", name="查看示例报告", exact=True).click()
    assert page.get_by_role("region", name="示例检查报告").count() == 0
    assert page.get_by_text("预设示例", exact=True).is_visible()
    assert page.get_by_text("示例：1 处引用指向已移除页面", exact=True).is_visible()
    assert page.get_by_role("button", name="修复失效引用", exact=True).count() == 0


def test_new_empty_knowledge_base(page):
    open_knowledge(page)
    page.get_by_role("button", name="新建知识库", exact=True).click()
    page.get_by_role("textbox", name="知识库名称", exact=True).fill("验收知识库")
    page.get_by_role("button", name="创建知识库", exact=True).click()
    page.get_by_role("heading", name="从第一份资料开始", exact=True).wait_for()
    assert page.get_by_role("heading", name="从第一份资料开始", exact=True).is_visible()
    assert "验收知识库" in page.get_by_role("combobox", name="选择知识库").inner_text()
    page.reload()
    page.get_by_role("button", name="阅读知识：Agent 分层架构", exact=True).wait_for()
    assert page.locator(".knowledge-card").count() == 6


def test_reader_sources_history_and_escape(page):
    open_knowledge(page)
    page.get_by_role("button", name="阅读知识：Agent 分层架构", exact=True).click()
    reader = page.get_by_role("region", name="知识页面详情", exact=True)
    reader.get_by_role("button", name=re.compile("^Icarus 架构说明.md")).click()
    assert page.get_by_role("region", name="资料详情").get_by_role("heading", name="Icarus 架构说明.md", exact=True).is_visible()
    page.get_by_role("button", name="返回知识页面", exact=True).click()
    assert reader.get_by_role("heading", name="Agent 分层架构", exact=True).is_visible()
    page.get_by_role("button", name="编辑页面", exact=True).click()
    page.get_by_role("textbox", name="知识页面正文").fill("不可保存的内容")
    page.get_by_role("textbox", name="知识页面正文").press("Escape")
    assert page.get_by_role("textbox", name="知识页面正文").count() == 0
    assert reader.get_by_role("heading", name="设计原则", exact=True).is_visible()
    page.go_back()
    page.get_by_role("region", name="资料详情").wait_for()
    assert page.get_by_role("heading", name="Icarus 架构说明.md", exact=True).is_visible()
    page.reload()
    page.get_by_role("region", name="资料详情").wait_for()
    assert page.get_by_role("heading", name="Icarus 架构说明.md", exact=True).is_visible()


def test_filters_and_deletion_recompute_relations(page):
    open_knowledge(page)
    page.get_by_role("textbox", name="搜索知识内容…").fill("没有匹配的页面xyz")
    assert page.get_by_role("heading", name="没有匹配的知识页面", exact=True).is_visible()
    page.get_by_role("button", name="清除筛选", exact=True).last.click()
    page.get_by_role("textbox", name="搜索知识内容…").fill("领域插件")
    assert page.locator(".knowledge-card").count() == 1
    page.get_by_role("tab", name=re.compile("^原始资料")).click()
    assert page.locator(".source-table tbody tr").count() == 6
    pick_option(page, "筛选内容类型", "PDF")
    assert page.locator(".source-table tbody tr").count() == 3
    page.get_by_role("button", name="清除筛选", exact=True).click()
    page.get_by_role("button", name="查看资料：Icarus 架构说明.md", exact=True).click()
    page.get_by_role("button", name="移除资料", exact=True).click()
    assert page.locator(".delete-impact strong").all_text_contents() == ["3", "2"]
    page.get_by_role("button", name="确认移除", exact=True).click()
    page.get_by_role("tab", name="来源关联", exact=True).click()
    assert page.locator(".relation-item").count() == 1
    assert page.locator(".relation-node").count() == 4
    assert page.locator(".relation-item").get_by_text("插件开发指南.pdf", exact=True).is_visible()


def test_import_validation_and_duplicate_files(page):
    open_knowledge(page)
    page.get_by_role("button", name="导入资料", exact=True).click()
    file_input = page.get_by_label("选择资料文件")
    file_input.set_input_files({"name": "unsupported.exe", "mimeType": "application/octet-stream", "buffer": b"demo"})
    assert page.get_by_role("alert").inner_text() == "支持 PDF、Markdown、TXT 和 DOCX 文件。"
    file_input.set_input_files({"name": "Icarus 架构说明.md", "mimeType": "text/markdown", "buffer": b"demo"})
    page.get_by_role("button", name="开始导入", exact=True).click()
    assert page.get_by_role("alert").inner_text() == "所选文件已存在，可在资料中重新编译。"


def test_mobile_reading_has_return_and_no_overflow(page):
    page.set_viewport_size({"width": 390, "height": 844})
    open_knowledge(page)
    page.get_by_role("button", name="阅读知识：Agent 分层架构", exact=True).click()
    assert page.get_by_role("button", name="返回知识页面列表", exact=True).is_visible()
    assert page.locator(".reading-paper").bounding_box()["width"] > 300
    assert page.locator(".shell-main").evaluate("el => el.scrollWidth <= el.clientWidth")
    page.get_by_role("button", name="返回知识页面列表", exact=True).click()
    page.get_by_role("tab", name=re.compile("^原始资料")).click()
    assert page.locator(".source-table").bounding_box()["width"] <= 354
    assert page.locator(".shell-main").evaluate("el => el.scrollWidth <= el.clientWidth")
    page.get_by_role("tab", name="来源关联", exact=True).click()
    assert page.locator(".relation-item").count() == 4
    assert page.locator(".relation-list").is_visible()
