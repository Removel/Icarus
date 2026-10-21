from pathlib import Path
import json
import re

import pytest
from playwright.sync_api import expect


def fake_openkb(page, broken_sources=False, content=None, graph_data=None):
    calls = []
    nodes = [
        ("summaries/paper", "paper", ["sources/paper.json"]),
        ("concepts/agent", "Agent 约束", ["summaries/paper"]),
        ("concepts/runtime", "插件运行时", ["summaries/paper"]),
        ("concepts/orphan", "独立页面", []),
    ]
    if broken_sources:
        nodes[-1] = ("concepts/orphan", "独立页面", ["raw/duplicate.pdf", "raw/missing.pdf"])
    graph = {"nodes": [{"id": id, "label": label, "type": "Concept", "description": label,
                        "sources": sources, "in": 0, "out": 0} for id, label, sources in nodes],
             "edges": [{"source": "concepts/agent", "target": "concepts/runtime"}], "types": ["Concept"]}
    if graph_data is not None:
        graph = graph_data
    contents = {id: f"# {label}\n\n正文：{label}。" for id, label, _ in nodes}
    if content is not None:
        contents['concepts/agent'] = content

    reports = []

    def respond(route):
        path = route.request.url.split('/api/v1/')[-1]
        body = route.request.post_data_json if route.request.method != 'GET' and path != 'add' and route.request.post_data else {}
        calls.append((path, body))
        if path == 'kbs': payload = {"knowledge_bases": [{"name": "测试知识库"}, {"name": "其他知识库"}]}
        elif path == 'list': payload = {"documents": [{"hash": "hash-paper", "name": "paper.pdf", "doc_name": "paper", "source_path": "wiki/sources/paper.json", "display_type": "PDF", "pages": 3}] + ([
                                            {"hash": "dup-1", "name": "duplicate.pdf", "doc_name": "duplicate-1", "source_path": "wiki/sources/duplicate-1.json", "display_type": "PDF", "pages": 2},
                                            {"hash": "dup-2", "name": "duplicate.pdf", "doc_name": "duplicate-2", "source_path": "wiki/sources/duplicate-2.json", "display_type": "PDF", "pages": 2},
                                        ] if broken_sources else []),
                                        "summaries": ["paper"], "concepts": ["agent", "runtime", "orphan"], "entities": [], "reports": list(reports)}
        elif path == 'graph': payload = graph
        elif path == 'page':
            if route.request.method == 'PUT':
                contents[body['path']] = body['content']
                payload = {"status": "saved", "content": body['content'], "ghosts_stripped": []}
            else: payload = {"path": body['path'], "content": contents[body['path']]}
        elif path == 'document/source': payload = {"hash": "hash-paper", "name": "paper.pdf", "doc_name": "paper", "type": "pdf", "format": "markdown", "content": "# 论文原文\n\n原始论证。", "pages": 3}
        elif path == 'lint':
            filename = f'lint_20261001_1200{len(reports):02}.md'
            reports.append(filename)
            contents['reports/' + filename] = '# 检查报告\n\n## 结构报告\n\n无结构问题。'
            payload = {"skipped": False, "message": "检查完成", "structural_report": "# 结构报告\n\n无结构问题。", "knowledge_report": None, "report_path": '/kb/wiki/reports/' + filename}
        elif path == 'report/delete':
            reports.remove(body['path'].removeprefix('reports/'))
            del contents[body['path']]
            payload = {"status": "deleted", "target": body['path']}
        elif path == 'remove': payload = {"status": "dry_run" if body['dry_run'] else "removed", "actions": [{"tag": "delete", "target": "summaries/paper"}], "pageindex_error": None}
        elif path == 'recompile': payload = {"status": "done", "recompiled": 1, "skipped": 0, "docs": [{"status": "ok"}]}
        elif path == 'init': payload = {"kb": body['kb'], "created": True}
        elif path == 'add': payload = {"added_count": 1, "skipped_count": 0, "failed_count": 0,
                                      "files": [{"original_name": "notes.md", "status": "added", "message": "Imported"}]}
        else: raise AssertionError(f"Unhandled OpenKB endpoint: {path}")
        route.fulfill(status=200, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))

    page.route('**/api/v1/**', respond)
    return calls


def open_knowledge(page):
    page.get_by_role('link', name='知识库', exact=True).click()
    page.get_by_role('link', name='知识页面', exact=True).click()
    page.get_by_role('region', name='知识页面列表').wait_for()


def open_graph(page):
    page.get_by_role('link', name='知识页面', exact=True).click()
    page.locator('.knowledge-card-main').first.click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    page.get_by_role('dialog', name='文档关联', exact=True).get_by_role('button', name='close', exact=True).click()


def test_evidence_map_explains_real_links_and_source_chain(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('button', name='阅读知识：Agent 约束').click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    dialog = page.get_by_role('dialog', name='文档关联', exact=True)
    expect(dialog.get_by_role('heading', name='Agent 约束', exact=True)).to_be_visible()
    expect(dialog.get_by_role('region', name='内容来源').get_by_role('button')).to_have_count(1)
    expect(dialog.get_by_role('region', name='引用的页面').get_by_role('button')).to_have_count(1)
    expect(dialog.get_by_text('共同来源线索')).to_have_count(0)
    expect(dialog.get_by_role('button', name='查看关联', exact=True)).to_have_count(0)
    dialog.get_by_role('region', name='引用的页面').get_by_role('button', name='插件运行时').click()
    reader = page.get_by_role('dialog', name='知识页面详情', exact=True)
    expect(reader.get_by_role('heading', name='插件运行时', exact=True)).to_be_visible()
    reader.get_by_role('button', name='close', exact=True).click()
    dialog.get_by_role('region', name='内容来源').get_by_role('button', name='paper', exact=True).click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    page.reload(wait_until='domcontentloaded')
    expect(dialog.get_by_role('heading', name='paper', exact=True)).to_be_visible()
    dialog.get_by_role('region', name='内容来源').get_by_role('button', name='paper.pdf', exact=True).click()
    expect(page.get_by_role('heading', name='论文原文', exact=True)).to_be_visible()
    assert any(path == 'document/source' and body['hash'] == 'hash-paper' for path, body in calls)


def test_quality_report_and_remove_preview_use_service(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('link', name='质量检查', exact=True).click()
    assert not any(path == 'lint' for path, _ in calls)
    page.get_by_role('button', name='开始检查', exact=True).click()
    expect(page.get_by_text('检查完成', exact=True)).to_be_visible()
    expect(page.locator('.quality-workspace')).not_to_contain_text('检查完成，报告已保存到知识库。')
    page.get_by_role('button', name='查看报告', exact=True).click()
    page.get_by_role('heading', name='结构报告', exact=True, level=2).wait_for()
    assert sum(path == 'lint' for path, _ in calls) == 1
    assert page.get_by_text('无结构问题。').is_visible()
    page.keyboard.press('Escape')
    page.get_by_role('link', name='知识页面', exact=True).click()
    page.get_by_role('link', name='质量检查', exact=True).click()
    page.get_by_role('button', name='查看报告', exact=True).click()
    page.get_by_text('无结构问题。').wait_for()
    assert page.get_by_text('无结构问题。').is_visible()
    page.keyboard.press('Escape')
    assert sum(path == 'lint' for path, _ in calls) == 1
    page.get_by_role('link', name='原始资料', exact=True).click()
    page.get_by_role('button', name='查看资料：paper.pdf').click()
    page.get_by_role('button', name='资料操作').click()
    page.get_by_role('menuitem', name='移除资料', exact=True).click()
    page.get_by_text('删除页面 · paper', exact=True).wait_for()
    page.get_by_text('服务操作详情 · 1 项', exact=True).click()
    page.get_by_text(re.compile('delete.*summaries/paper')).wait_for()
    assert any(path == 'remove' and body['dry_run'] for path, body in calls)
    assert any(path == 'remove' and body['identifier'] == 'hash-paper' for path, body in calls)
    page.get_by_role('button', name='保留资料', exact=True).click()
    assert not any(path == 'remove' and not body['dry_run'] for path, body in calls)


def test_mobile_evidence_has_canvas_and_readable_inspector(page):
    fake_openkb(page)
    page.set_viewport_size({"width": 390, "height": 844})
    open_knowledge(page)
    open_graph(page)
    page.get_by_role('link', name='知识页面', exact=True).click()
    page.get_by_role('button', name='阅读知识：Agent 约束', exact=True).click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    assert page.locator('.relation-title').is_visible()
    assert page.locator('.relation-section').first.is_visible()
    assert page.locator('.shell-main').evaluate('el => el.scrollWidth <= el.clientWidth')


def test_edit_import_and_recompile_follow_service_results(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('button', name='阅读知识：Agent 约束').click()
    page.get_by_role('button', name='编辑页面').click()
    page.get_by_role('textbox', name='知识页面正文').fill('# Agent 约束\n\n已复核。')
    page.get_by_role('button', name='保存页面').click()
    page.get_by_text('已复核。').wait_for()
    assert any(path == 'page' and body.get('content') == '# Agent 约束\n\n已复核。' for path, body in calls)
    page.get_by_role('dialog').get_by_role('button', name='close', exact=True).click()
    page.get_by_role('link', name='原始资料', exact=True).click()
    page.get_by_role('button', name='导入资料').click()
    page.get_by_label('选择资料文件').set_input_files({"name": "notes.md", "mimeType": "text/markdown", "buffer": b'# notes'})
    page.get_by_role('button', name='开始导入').click()
    page.get_by_text('已完成 1 · 已跳过 0 · 失败 0').wait_for()
    assert any(path == 'add' for path, _ in calls)
    page.get_by_role('button', name='关闭').click()
    page.get_by_role('link', name='原始资料', exact=True).click()
    page.get_by_role('button', name='查看资料：paper.pdf').click()
    page.get_by_role('button', name='资料操作').click()
    page.get_by_role('menuitem', name='重新生成知识', exact=True).click()
    assert not any(path == 'recompile' for path, _ in calls)
    page.get_by_role('dialog').get_by_text(re.compile('已保存的人工修改也可能被覆盖')).wait_for()
    page.get_by_role('button', name='确认重新生成', exact=True).click()
    assert any(path == 'recompile' and body['doc_name'] == 'hash-paper' for path, body in calls)


def test_missing_and_ambiguous_sources_do_not_create_false_links(page):
    fake_openkb(page, broken_sources=True)
    open_knowledge(page)
    open_graph(page)
    page.get_by_role('link', name='知识页面', exact=True).click()
    page.get_by_role('button', name='阅读知识：独立页面', exact=True).click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    group = page.get_by_role('dialog', name='文档关联', exact=True).get_by_role('region', name='内容来源')
    expect(group.get_by_role('button', name=re.compile('duplicate.pdf'))).to_be_disabled()
    expect(group.get_by_text('有多份同名资料，暂时无法确定来源')).to_be_visible()
    expect(group.get_by_role('button', name=re.compile('missing.pdf'))).to_be_disabled()
    expect(group.get_by_text('未找到该来源')).to_be_visible()


def test_service_failure_is_shown_as_error_not_empty_graph(page):
    page.route('**/api/v1/kbs', lambda route: route.fulfill(status=503, json={"detail": "unavailable"}))
    page.get_by_role('link', name='知识库', exact=True).click()
    page.get_by_role('alert').get_by_text('知识服务请求失败（503），请稍后重试。').wait_for()
    assert page.get_by_role('button', name='重试加载').is_visible()
    assert page.get_by_text('知识库为空').count() == 0


def test_source_derivation_includes_summary_chain_but_not_unrelated_pages(page):
    fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('link', name='原始资料', exact=True).click()
    expect(page.locator('.source-card').first).to_contain_text('3 篇')
    page.get_by_role('button', name='查看资料：paper.pdf').click()
    related = page.get_by_role('region', name='派生知识')
    expect(related.get_by_role('button')).to_have_count(3)
    expect(related).to_contain_text('直接引用资料')
    expect(related).to_contain_text('经来源链派生')
    expect(related).not_to_contain_text('独立页面')
    related.get_by_role('button', name=re.compile('Agent 约束')).click()
    expect(page.get_by_role('heading', name='Agent 约束', exact=True)).to_be_visible()
    page.go_back()
    expect(page.get_by_role('region', name='资料详情')).to_be_visible()


def test_graph_failure_does_not_block_documents_import_or_reading(page):
    fake_openkb(page)
    page.route('**/api/v1/graph', lambda route: route.fulfill(status=500, json={'detail': 'graph unavailable'}))
    open_knowledge(page)
    page.get_by_role('link', name='原始资料', exact=True).click()
    expect(page.locator('.source-card')).to_have_count(1)
    expect(page.locator('.knowledge-grid')).to_contain_text('暂不可用')
    assert page.get_by_role('button', name='导入资料', exact=True).is_enabled()
    page.get_by_role('button', name='导入资料', exact=True).click()
    expect(page.get_by_role('dialog', name='导入资料')).to_be_visible()
    page.get_by_role('button', name='关闭', exact=True).click()
    page.get_by_role('button', name='查看资料：paper.pdf').click()
    expect(page.get_by_role('heading', name='论文原文', exact=True)).to_be_visible()
    assert page.get_by_role('button', name='查看关联', exact=True).is_disabled()


def test_quality_failure_can_retry_without_restarting_on_navigation(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.route('**/api/v1/lint', lambda route: route.fulfill(status=500, json={'detail': 'failed'}))
    page.get_by_role('link', name='质量检查', exact=True).click()
    assert not any(path == 'lint' for path, _ in calls)
    page.get_by_role('button', name='开始检查', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('500')
    page.unroute('**/api/v1/lint')
    page.get_by_role('button', name='开始检查', exact=True).click()
    page.get_by_role('button', name='查看报告', exact=True).click()
    expect(page.get_by_role('heading', name='结构报告', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    page.get_by_role('link', name='记忆', exact=True).click()
    page.get_by_role('link', name='知识库', exact=True).click()
    page.get_by_role('button', name='查看报告', exact=True).click()
    expect(page.get_by_text('无结构问题。')).to_be_visible()
    assert sum(path == 'lint' for path, _ in calls) == 1


def test_regeneration_can_be_cancelled_without_calling_service(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('link', name='原始资料', exact=True).click()
    page.get_by_role('button', name='查看资料：paper.pdf').click()
    page.get_by_role('button', name='资料操作', exact=True).click()
    page.get_by_role('menuitem', name='重新生成知识', exact=True).click()
    expect(page.get_by_role('dialog', name='重新生成知识？', exact=True)).to_contain_text('当前可追溯的知识有 3 篇')
    page.get_by_role('button', name='保留现有知识', exact=True).click()
    expect(page.get_by_role('dialog', name='重新生成知识？', exact=True)).not_to_be_visible()
    assert not any(path == 'recompile' for path, _ in calls)


@pytest.mark.parametrize('destination', ['close', 'escape', 'back', 'explore', 'cancel', 'mask'])
def test_unsaved_page_protects_every_navigation_path(page, destination):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('button', name='阅读知识：Agent 约束').click()
    page.get_by_role('button', name='编辑页面').click()
    editor = page.get_by_role('textbox', name='知识页面正文')
    editor.fill('# Agent 约束\n\n待确认的草稿')
    original_url = page.url

    def leave():
        if destination == 'close':
            page.get_by_role('dialog', name='知识页面详情', exact=True).get_by_role('button', name='close', exact=True).click()
        elif destination == 'escape':
            page.get_by_role('button', name='查看关联', exact=True).focus()
            page.keyboard.press('Escape')
        elif destination == 'mask':
            page.mouse.click(4, 4)
        elif destination == 'back':
            page.go_back()
        elif destination == 'explore':
            page.get_by_role('button', name='查看关联', exact=True).click()
        else:
            page.get_by_role('button', name='取消编辑').click()

    leave()
    dialog = page.get_by_role('dialog', name='放弃未保存的修改？', exact=True)
    dialog.get_by_role('heading', name='放弃未保存的修改？').wait_for()
    assert page.url == original_url
    dialog.get_by_role('button', name='继续编辑').click()
    expect(dialog).not_to_be_visible()
    # Semi debounces repeated close requests for 100 ms, including Escape and mask clicks.
    page.wait_for_timeout(120)
    expect(editor).to_have_value('# Agent 约束\n\n待确认的草稿')
    leave()
    dialog.get_by_role('button', name='放弃修改').click()
    dialog.wait_for(state='hidden')
    expect(editor).to_have_count(0)
    assert not any(path == 'page' and 'content' in body for path, body in calls)
    if destination == 'explore':
        expect(page.get_by_role('dialog', name='文档关联', exact=True)).to_be_visible()
        expect(page.locator('.document-node.is-selected')).to_contain_text('Agent 约束')
    elif destination in ('close', 'escape', 'back', 'mask'):
        expect(page.get_by_role('region', name='知识页面列表')).to_be_visible()


def test_search_is_stable_after_reading_and_filters_persist_per_list(page):
    fake_openkb(page, content='# 正文新标题\n\n正文独有词')
    open_knowledge(page)
    search = page.get_by_role('textbox', name='搜索标题与摘要…')
    search.fill('正文')
    expect(page.get_by_role('heading', name='没有匹配的知识页面')).to_be_visible()
    search.fill('')
    page.get_by_role('button', name='阅读知识：Agent 约束').click()
    page.get_by_text('正文独有词', exact=True).wait_for()
    page.get_by_role('dialog').get_by_role('button', name='close', exact=True).click()
    search.fill('正文')
    expect(page.get_by_role('heading', name='没有匹配的知识页面')).to_be_visible()
    search.fill('Agent')
    page.get_by_role('combobox', name='筛选内容类型').click()
    page.locator('.semi-select-option').filter(has_text=re.compile('^概念$')).click()
    page.get_by_role('link', name='原始资料', exact=True).click()
    source_search = page.get_by_role('textbox', name='搜索资料名称…')
    expect(source_search).to_have_value('')
    source_search.fill('paper')
    page.get_by_role('link', name='知识页面', exact=True).click()
    expect(search).to_have_value('Agent')
    expect(page.get_by_role('combobox', name='筛选内容类型')).to_contain_text('概念')
    page.get_by_role('button', name='移除类型筛选').click()
    expect(search).to_have_value('Agent')
    page.get_by_role('link', name='原始资料', exact=True).click()
    expect(source_search).to_have_value('paper')
    open_graph(page)
    expect(page.get_by_role('textbox', name='搜索知识与资料…')).to_have_count(0)


@pytest.mark.parametrize('width', [360, 390, 768])
def test_tabs_sources_and_reader_index_fit_narrow_screens(page, width):
    fake_openkb(page)
    page.set_viewport_size({'width': width, 'height': 844})
    open_knowledge(page)
    nav = page.get_by_role('navigation', name='知识库分类').bounding_box()
    for name in ['知识页面', '原始资料', '文档关联', '质量检查']:
        box = page.get_by_role('link', name=name, exact=True).bounding_box()
        assert box['x'] >= nav['x']
        assert box['x'] + box['width'] <= nav['x'] + nav['width']
    page.get_by_role('link', name='原始资料', exact=True).click()
    assert page.locator('.source-table .status').count() == 0
    assert page.locator('.shell-main').evaluate('el => el.scrollWidth <= el.clientWidth')
    page.get_by_role('button', name='查看资料：paper.pdf').click()
    expect(page.get_by_role('button', name='资料索引', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name=re.compile('返回.*列表'))).to_have_count(0)
    assert page.locator('.reading-paper').bounding_box()['width'] >= width - 150


def test_markdown_outline_links_and_untrusted_html(page):
    fake_openkb(page, content='''# Agent 约束

## 使用方式

**重点** 与 [网站](https://example.com)、[危险链接](javascript:alert(1))。

> 引用内容

1. 第一步
2. 第二步

| 名称 | 说明 |
| --- | --- |
| Agent | 执行任务 |

```python
print("[[concepts/runtime]]")
```

[[concepts/runtime|阅读插件]] 和 [[concepts/missing|失效页面]]

<script>window.markdownUnsafe = true</script>
<img src=x onerror="window.markdownUnsafe = true">

## 参考说明

参考内容
''')
    open_knowledge(page)
    page.get_by_role('button', name='阅读知识：Agent 约束').click()
    page.get_by_role('heading', name='使用方式', exact=True).wait_for()
    assert page.get_by_role('heading', name='Agent 约束', exact=True).count() == 1
    assert page.locator('.reading strong').inner_text() == '重点'
    assert page.locator('.reading blockquote').inner_text() == '引用内容'
    assert page.locator('.reading ol li').count() == 2
    assert page.locator('.reading table').is_visible()
    assert '[[concepts/runtime]]' in page.locator('.reading pre code').inner_text()
    assert page.get_by_role('link', name='网站').get_attribute('rel') == 'noopener noreferrer'
    assert page.get_by_role('link', name='危险链接').count() == 0
    assert page.get_by_role('button', name='失效页面').count() == 0
    assert page.evaluate('window.markdownUnsafe === undefined')
    original_url = page.url
    page.get_by_text('文章目录 · 2', exact=True).click()
    page.get_by_role('navigation', name='文章目录').get_by_role('button', name='参考说明').click()
    assert page.url == original_url
    page.get_by_role('button', name='阅读插件', exact=True).click()
    expect(page.get_by_role('heading', name='插件运行时', exact=True)).to_be_visible()

def test_reader_modal_preserves_list_filter_and_focus(page):
    fake_openkb(page)
    open_knowledge(page)
    search = page.get_by_role('textbox', name='搜索标题与摘要…')
    search.fill('Agent')
    opener = page.get_by_role('button', name='阅读知识：Agent 约束')
    opener.click()
    reader = page.get_by_role('dialog', name='知识页面详情', exact=True)
    expect(reader).to_be_visible()
    assert page.locator('.knowledge-background').get_attribute('inert') is not None
    page.reload()
    expect(reader).to_be_visible()
    reader.get_by_role('button', name='close', exact=True).click()
    expect(reader).not_to_be_visible()
    # A fresh page has no session filters; opening and closing preserves the current ones.
    search.fill('Agent')
    opener.click()
    reader.get_by_role('button', name='close', exact=True).click()
    expect(search).to_have_value('Agent')
    expect(opener).to_be_focused()


def test_graph_canvas_navigation_and_layout_survive_reader(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('link', name='文档关联', exact=True).click()
    expect(page.locator('.react-flow__node')).to_have_count(5)
    page.get_by_role('button', name='全部连线', exact=True).click()
    expect(page.locator('.react-flow__edge')).to_have_count(4)
    assert not any(path == 'lint' for path, _ in calls)
    node = page.locator('.react-flow__node[aria-label="选择节点：Agent 约束"]')
    expect(node).to_be_visible()
    box = node.bounding_box()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    page.mouse.down()
    page.mouse.move(box['x'] + box['width'] / 2 + 35, box['y'] + box['height'] / 2 + 25, steps=8)
    page.mouse.up()
    position = node.evaluate('el => el.style.transform')
    page.get_by_role('button', name='放大画布', exact=True).click()
    viewport = page.locator('.react-flow__viewport').get_attribute('style')
    expect(page.get_by_role('textbox', name='搜索知识与资料…')).to_have_count(0)
    expect(page.get_by_role('combobox', name='选择知识库')).to_have_count(0)
    expect(page.get_by_role('button', name='知识库设置')).to_have_count(0)
    expect(page.locator('.react-flow__minimap')).to_have_count(0)
    assert page.locator('.document-canvas').bounding_box()['height'] > 900
    node.hover()
    expect(page.locator('.react-flow__edge.is-highlighted')).to_have_count(2)
    expect(page.locator('.react-flow__edge.is-muted')).to_have_count(2)
    page.get_by_role('button', name='重置布局', exact=True).hover()
    expect(page.locator('.react-flow__edge.is-muted')).to_have_count(0)
    expect(page.get_by_role('button', name='全部内容', exact=True)).to_have_count(0)
    node.focus()
    node.press('Enter')
    expect(page.locator('.evidence-center')).to_have_count(0)
    reader = page.get_by_role('dialog', name='知识页面详情', exact=True)
    expect(reader).to_be_visible()
    assert page.get_by_role('link', name='文档关联', exact=True).get_attribute('aria-current') == 'page'
    reader.locator('.reading-references').get_by_role('button').click()
    expect(reader.get_by_role('heading', name='paper', exact=True)).to_be_visible()
    reader.get_by_role('button', name='close', exact=True).click()
    expect(reader).not_to_be_visible()
    expect(node).to_be_visible()
    assert node.evaluate('el => el.style.transform') == position
    assert page.locator('.react-flow__viewport').get_attribute('style') == viewport
    page.get_by_role('button', name='重置布局', exact=True).click()
    page.wait_for_function('([el, position]) => el.style.transform !== position', arg=[node.element_handle(), position])


def test_graph_edge_keyboard_selection_explains_direction(page):
    fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('link', name='文档关联', exact=True).click()
    page.get_by_role('button', name='全部连线', exact=True).click()
    edge = page.locator('.react-flow__edge[aria-label="正文引用"]')
    edge.focus()
    edge.press('Enter')
    dialog = page.get_by_role('dialog', name='文档关联', exact=True)
    expect(dialog.locator('.relation-title')).to_have_text('Agent 约束')
    expect(dialog.get_by_role('region', name='引用的页面').locator('.is-highlighted')).to_contain_text('插件运行时')
    dialog.get_by_role('button', name='close', exact=True).click()
    expect(page.locator('.react-flow__edge')).to_have_count(4)
    edge.focus()
    edge.press('Delete')
    expect(page.locator('.react-flow__edge')).to_have_count(4)


@pytest.mark.parametrize('kind', ['page', 'source'])
def test_reader_explore_opens_relation_modal(page, kind):
    fake_openkb(page)
    open_knowledge(page)
    if kind == 'source':
        page.get_by_role('link', name='原始资料', exact=True).click()
        page.get_by_role('button', name='查看资料：paper.pdf').click()
    else:
        page.get_by_role('button', name='阅读知识：Agent 约束').click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    expect(page.get_by_role('dialog', name='文档关联', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='查看关系依据', exact=True)).to_have_count(0)
    page.get_by_role('dialog', name='文档关联', exact=True).get_by_role('button', name='close', exact=True).click()
    expect(page.locator('.document-node.is-selected')).to_have_count(1)
    expect(page.locator('.evidence-inspector')).to_have_count(0)
    canvas = page.locator('.document-canvas').bounding_box()
    layout = page.locator('.evidence-layout').bounding_box()
    assert abs(canvas['width'] - layout['width']) <= 2


def test_quality_reports_survive_reload_export_and_delete(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('link', name='质量检查', exact=True).click()
    page.get_by_role('button', name='开始检查', exact=True).click()
    expect(page.locator('.quality-report-row')).to_have_count(1)
    assert not page.get_by_text('无结构问题。').is_visible()
    page.reload()
    expect(page.locator('.quality-report-row')).to_have_count(1)
    with page.expect_download() as download:
        page.get_by_role('button', name='导出', exact=True).click()
    assert download.value.suggested_filename.endswith('.md')
    assert '无结构问题' in Path(download.value.path()).read_text(encoding='utf-8')
    page.get_by_role('button', name='重新检查', exact=True).click()
    expect(page.locator('.quality-report-row')).to_have_count(2)
    page.get_by_role('button', name='删除报告', exact=True).first.click()
    page.get_by_role('button', name='确认删除报告', exact=True).click()
    expect(page.locator('.quality-report-row')).to_have_count(1)
    page.reload()
    expect(page.locator('.quality-report-row')).to_have_count(1)
    assert sum(path == 'lint' for path, _ in calls) == 2


def test_quality_actions_and_empty_state_have_separate_areas(page):
    fake_openkb(page)
    page.get_by_role('link', name='知识库', exact=True).click()
    page.get_by_role('link', name='质量检查', exact=True).click()
    expect(page.get_by_role('heading', name='还没有检查报告')).to_be_visible()
    context = page.locator('.knowledge-context').bounding_box()
    actions = page.locator('.quality-start').bounding_box()
    reports = page.locator('.quality-empty').bounding_box()
    assert context['y'] + context['height'] <= actions['y']
    assert actions['y'] + actions['height'] <= reports['y']
    assert page.get_by_role('combobox', name='选择知识库').bounding_box()['width'] <= 200

@pytest.mark.parametrize('motion', ['reduce', 'no-preference'])
def test_quality_export_preserves_page_and_report_dialog(page, motion):
    page.emulate_media(reduced_motion=motion)
    fake_openkb(page)
    open_knowledge(page)
    page.get_by_role('link', name='质量检查', exact=True).click()
    expect(page.get_by_role('heading', name='还没有检查报告')).to_be_visible()
    assert page.get_by_text('检查知识内容与引用关系，生成可回看的检查报告。').count() == 0
    assert page.locator('.quality-empty p').count() == 0
    page.get_by_role('button', name='开始检查', exact=True).click()
    page.get_by_role('button', name='查看报告', exact=True).click()
    dialog = page.get_by_role('dialog', name='质量检查报告', exact=True)
    expect(dialog).to_be_visible()
    # Visibility can precede the end of the modal's scale-in animation.
    dialog.evaluate('''element => Promise.all(
        element.getAnimations().map(animation => animation.finished.catch(() => {}))
    )''')
    before = dialog.bounding_box()
    url = page.url
    with page.expect_download() as download:
        dialog.get_by_role('button', name='导出报告', exact=True).click()
    assert '无结构问题' in Path(download.value.path()).read_text(encoding='utf-8')
    page.wait_for_timeout(1200)
    expect(dialog).to_be_visible()
    assert page.url == url
    assert dialog.bounding_box() == before
    assert page.locator('.quality-start .semi-spin').count() == 0
    expect(page.locator('.quality-report-row')).to_have_count(1)
