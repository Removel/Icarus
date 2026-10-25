import pytest
from playwright.sync_api import expect

from test.navigation import open_settings
from test.packages.chat_app.test_chat import gateway


def snapshot(status="complete", *, pages=3, total=24):
    sources = [{"title": f"资料标题 {i}", "url": f"https://site{i % 5}.example/article/{i}",
                "snippet": f"真实返回的搜索摘要 {i}", "siteName": f"站点 {i % 5}", "citeIndex": i + 1,
                "siteIcon": "https://favicon-third-party.invalid/icon.png"} for i in range(18)]
    return {"status": status, "total": total, "queries": ["最新资料 官方来源", "最新资料 数据对比"],
            "sources": sources, "pages": [{"url": row["url"], "title": row["title"], "status": "done", "contentLength": 1234} for row in sources[:pages]],
            "browsing": False, "notice": ""}


@pytest.mark.parametrize("width", [390, 1440])
def test_search_sources_fetch_nodes_citations_and_history(page, width):
    page.set_viewport_size({"width": width, "height": 900})
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    state, emit = gateway(page)
    emit("user.message", {"text": "查询最新资料"})
    emit("task.started", {})
    emit("web_search.updated", snapshot())
    emit("assistant.message", {"step": 1, "text": "最新结论 [1]。\n\n第二条证据 [2]。\n\n`代码 [1]`，未知来源 [99]。"})
    emit("task.finished", {"status": "completed"})
    process = page.locator(".chat-process")
    process.locator(":scope > summary").click()
    expect(page.locator(".search-step")).to_contain_text("搜索到 24 个网页")
    expect(page.locator(".source-marks .source-mark")).to_have_count(4)
    expect(page.locator(".open-pages-head")).to_contain_text("浏览 3 个页面")
    expect(page.locator(".open-pages-list li")).to_have_count(3)
    assert page.locator(".open-pages-list a").first.get_attribute("href") == "https://site0.example/article/0"
    expect(page.locator(".web-citation")).to_have_count(2)
    page.get_by_role("button", name="查看来源 2", exact=True).click()
    dialog = page.get_by_role("dialog", name="联网来源", exact=True)
    expect(dialog).to_be_visible()
    assert dialog.evaluate("node => !node.closest('.md-body')")
    expect(dialog.locator(".web-sources-list li")).to_have_count(18)
    expect(dialog.locator("[data-selected] strong")).to_contain_text("资料标题 1")
    expect(dialog.locator('.web-sources-list')).to_have_css('list-style-type', 'none')
    page.screenshot(path=f"test-results/web-sources-{width}.png", full_page=True)
    dialog.get_by_role("button", name="关闭来源").click()
    page.screenshot(path=f"test-results/web-search-{width}.png", full_page=True)
    assert not any("favicon-third-party" in url for url in requests)
    page.reload()
    expect(page.locator(".chat-assistant")).to_contain_text("最新结论")
    page.locator(".chat-process > summary").click()
    expect(page.locator(".search-step")).to_contain_text("搜索到 24 个网页")
    expect(page.locator(".open-pages-list li")).to_have_count(3)
    assert len([r for r in state["records"] if r["type"] == "web_search.updated"]) == 1


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_citation_scrolls_to_selected_card_and_long_titles_fit(page, theme):
    page.set_viewport_size({'width': 390, 'height': 844})
    _, emit = gateway(page)
    page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
    data = snapshot()
    data['sources'][-1]['title'] = 'very-long-unbroken-title-' * 24
    emit('user.message', {'text': '查看最后一条来源'})
    emit('web_search.updated', data)
    emit('assistant.message', {'step': 1, 'text': '结论见 [18]。'})
    page.get_by_role('button', name='查看来源 18', exact=True).click()
    dialog = page.get_by_role('dialog', name='联网来源', exact=True)
    card = dialog.locator('[data-selected]')
    expect(card).to_be_in_viewport()
    assert dialog.locator('.web-sources-list').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=f'test-results/web-source-long-{theme}.png')
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role('button', name='查看来源 18', exact=True)).to_be_focused()


def test_queries_rotate_and_stop_survives_reload(page):
    state, emit = gateway(page)
    emit("user.message", {"text": "一个时效问题"})
    emit("task.started", {})
    progress = snapshot("searching", pages=0, total=0)
    progress["sources"] = []
    emit("web_search.updated", progress)
    page.locator(".chat-process > summary").click()
    expect(page.locator(".search-query")).to_have_text("最新资料 官方来源")
    expect(page.locator(".search-query")).to_have_text("最新资料 数据对比", timeout=3000)
    page.get_by_role("button", name="停止执行").click()
    expect(page.locator(".search-step")).to_have_text("搜索已停止")
    expect(page.locator(".open-pages")).to_have_count(0)
    page.reload()
    page.locator(".chat-process > summary").click()
    expect(page.locator(".search-step")).to_have_text("搜索已停止")
    assert any(call["method"] == "session.cancel" for call in state["calls"])


@pytest.mark.parametrize("status,total,notice,label", [
    ("error", 0, "联网搜索暂不可用，已继续正常回答。", "联网搜索暂不可用"),
    ("empty", 0, "", "未搜索到合适内容"),
    ("complete", 24, "网页正文暂不可用，本轮仅参考搜索摘要。", "搜索到 24 个网页"),
], ids=["search-error", "empty", "fetch-error"])
def test_failures_do_not_fake_browsing_or_interrupt_answer(page, status, total, notice, label):
    _, emit = gateway(page)
    progress = snapshot(status, pages=0, total=total)
    progress["notice"] = notice
    if total == 0:
        progress["sources"] = []
    emit("user.message", {"text": "失败时继续回答"})
    emit("web_search.updated", progress)
    emit("assistant.message", {"step": 1, "text": "本轮资料不足，仍然可以正常回答。"})
    emit("task.finished", {"status": "completed"})
    expect(page.locator(".chat-assistant")).to_contain_text("正常回答")
    page.locator(".chat-process > summary").click()
    expect(page.locator(".search-step")).to_contain_text(label)
    expect(page.locator(".open-pages")).to_have_count(0)
    if notice:
        expect(page.locator(".search-notice")).to_have_text(notice)


def test_search_preferences_are_captured_per_queued_message_and_time_is_local(page):
    state, emit = gateway(page)
    toggle = page.get_by_role("button", name="联网搜索", exact=True)
    expect(toggle).to_have_attribute("aria-pressed", "false")
    editor = page.get_by_role("textbox", name="发送消息")
    editor.fill("关闭搜索的第一条")
    editor.press("Enter")
    expect(page.locator(".chat-user")).to_contain_text("关闭搜索的第一条")
    first = next(call for call in state["calls"] if call["method"] == "session.submit")
    assert first["params"]["request_options"]["web_search"] is False
    clock = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    assert first["params"]["request_options"]["time_zone"] == clock
    assert first["params"]["request_options"]["local_time"][-6:-5] in ("+", "-")
    toggle.click()
    editor.fill("开启搜索的排队消息")
    editor.press("Enter")
    expect(page.get_by_label("待发送消息", exact=True)).to_contain_text("开启搜索的排队消息")
    toggle.click()
    emit("task.finished", {"status": "completed"})
    expect(page.locator(".chat-user")).to_contain_text(["关闭搜索的第一条", "开启搜索的排队消息"])
    calls = [call for call in state["calls"] if call["method"] == "session.submit"]
    assert len(calls) == 2
    assert calls[1]["params"]["request_options"]["web_search"] is True
    assert calls[0]["params"]["prompt"] == "关闭搜索的第一条"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_search_toggle_visibly_distinguishes_enabled_state(page, theme):
    gateway(page)
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    toggle = page.get_by_role("button", name="联网搜索", exact=True)
    toggle.hover()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    off_color = toggle.evaluate("node => getComputedStyle(node).color")
    off_border = toggle.evaluate("node => getComputedStyle(node).borderColor")
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    page.wait_for_function(
        "({color, border}) => { const node = document.querySelector('button[aria-pressed]'); "
        "const style = getComputedStyle(node); return style.color !== color && style.borderColor !== border; }",
        arg={"color": off_color, "border": off_border},
    )
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(toggle).to_have_css("color", off_color)
    expect(toggle).to_have_css("border-color", off_border)


def test_deep_search_and_real_six_page_overflow(page):
    _, emit = gateway(page)
    open_settings(page)
    deep = page.get_by_role("checkbox", name="深度检索（多页）")
    expect(deep).not_to_be_checked()
    deep.check()
    page.get_by_role("button", name="关闭设置", exact=True).click()
    emit("user.message", {"text": "深度检索"})
    emit("web_search.updated", snapshot(pages=6))
    emit("task.finished", {"status": "completed"})
    page.locator(".chat-process > summary").click()
    expect(page.locator(".open-pages-list li")).to_have_count(5)
    page.get_by_role("button", name="查看全部（6）").click()
    expect(page.locator(".open-pages-list li")).to_have_count(6)
    page.reload()
    expect(page.get_by_role("textbox", name="发送消息")).to_be_enabled()
    open_settings(page)
    expect(page.get_by_role("checkbox", name="深度检索（多页）")).to_be_checked()
