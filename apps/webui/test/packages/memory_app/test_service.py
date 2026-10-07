import math
import re

from playwright.sync_api import expect


def memory_page_size(page):
    # Observe a settled response and layout, including the initial card-height measurement.
    page.evaluate('window.memoryPageSettlement = undefined')
    page.wait_for_function('''() => {
        const board = document.querySelector('.memory-board');
        const footer = document.querySelector('.memory-pagination');
        if (!board || !footer || board.getAttribute('aria-busy') !== 'false') {
            window.memoryPageSettlement = undefined;
            return false;
        }
        const signature = [footer.textContent, board.clientWidth, board.clientHeight,
            board.querySelectorAll('.memory-entry').length].join('|');
        const previous = window.memoryPageSettlement;
        if (!previous || previous.signature !== signature) {
            window.memoryPageSettlement = {signature, time: performance.now()};
            return false;
        }
        return performance.now() - previous.time >= 300;
    }''')
    text = page.get_by_role('navigation', name='记忆分页').inner_text()
    return int(re.search(r'每页 (\d+) 条', text).group(1))


def seed_paged_memories(page, count=60):
    template = page.mem0_rows['mem_a8f2c1']
    for index in range(count):
        row = dict(template, id=f'page-{index}', memory=f'分页记忆 {index}',
                   updated_at=f'2026-10-02T{index // 60:02}:{index % 60:02}:00Z')
        page.mem0_rows[row['id']] = row


def test_service_update_preserves_metadata_and_survives_reload(page):
    page.locator('.memory-entry-open').first.click()
    page.get_by_role('button', name='修正内容', exact=True).click()
    page.get_by_role('textbox', name='修正记忆内容').fill('服务持久化的记忆')
    page.get_by_role('button', name='保存修改', exact=True).click()
    expect(page.locator('.memory-detail-text')).to_have_text('服务持久化的记忆')
    updates = [body for method, _, body, _ in page.mem0_calls if method == 'PUT']
    assert updates[0]['text'] == '服务持久化的记忆'
    assert updates[0]['metadata']['custom'] == 'preserve'
    assert any('/memories/page?' in url for _, _, _, url in page.mem0_calls)
    page.reload()
    expect(page.locator('.memory-detail-text')).to_have_text('服务持久化的记忆')


def test_failed_save_keeps_draft_and_does_not_change_list(page):
    original = page.locator('.memory-content').first.inner_text()
    page.route('**/api/mem0/memories/*', lambda route: route.fulfill(status=503, json={}) if route.request.method == 'PUT' else route.fallback())
    page.locator('.memory-entry-open').first.click()
    page.get_by_role('button', name='修正内容', exact=True).click()
    editor = page.get_by_role('textbox', name='修正记忆内容')
    editor.fill('失败时保留草稿')
    page.get_by_role('button', name='保存修改', exact=True).click()
    page.get_by_role('alert').filter(has_text='503').wait_for()
    assert editor.input_value() == '失败时保留草稿'
    assert page.locator('.memory-content').first.inner_text() == original


def test_batch_partial_failure_reports_committed_and_failed_items(page):
    page.route('**/api/mem0/memories/mem_a8f2c1', lambda route: route.fulfill(status=503, json={}) if route.request.method == 'PUT' else route.fallback())
    page.get_by_role('button', name='多选', exact=True).click()
    page.get_by_text('全选当前结果', exact=True).click()
    page.get_by_role('button', name='暂停使用（6）', exact=True).click()
    page.get_by_role('alert').filter(has_text='已完成 5 条，失败 1 条').wait_for()
    assert page.locator('.memory-entry.is-inactive').count() == 7


def test_create_contract_uses_service_id_and_raw_input(page):
    page.get_by_role('button', name='添加记忆', exact=True).click()
    page.get_by_role('textbox', name='记忆内容', exact=True).fill('手动记录原文')
    page.get_by_role('button', name='保存记忆', exact=True).click()
    expect(page.locator('.memory-detail-text')).to_have_text('手动记录原文')
    body = next(body for method, path, body, _ in page.mem0_calls if method == 'POST' and path == '/memories')
    assert body['infer'] is False
    assert body['messages'] == [{'role': 'user', 'content': '手动记录原文'}]
    assert body['agent_id'] == 'icarus'
    assert body['user_id'] == 'configured-user'
    assert body['run_id'] == 'global'
    page.reload()
    expect(page.locator('.memory-detail-text')).to_have_text('手动记录原文')


def test_history_pages_labels_diff_and_property_order(page):
    entries = [dict(event='UPDATE', old_memory='原文一致', new_memory='原文一致',
                    updated_at=f'2026-10-01T01:{i:02}:00Z',
                    changes={'expiration_date': {'before': None, 'after': '1970-01-01'}})
               for i in range(11)]
    entries.append(dict(event='UPDATE', old_memory='共同开头旧内容共同结尾', new_memory='共同开头新内容共同结尾',
                        updated_at='2026-10-01T02:00:00Z', changes={}))
    page.route('**/memories/*/history', lambda route: route.fulfill(json=entries))
    page.locator('.memory-entry-open').first.click()
    properties = page.locator('.memory-detail-properties').bounding_box()
    body = page.locator('.memory-detail-text').bounding_box()
    assert body['y'] + body['height'] <= properties['y']
    page.locator('.memory-history > summary').click()
    expect(page.locator('.timeline-item')).to_have_count(10)
    expect(page.locator('.memory-diff del')).to_have_text('旧')
    expect(page.locator('.memory-diff ins')).to_have_text('新')
    expect(page.locator('.timeline-item').first).to_contain_text('修正内容')
    expect(page.locator('.timeline-item').nth(1)).to_contain_text('暂停使用')
    expect(page.locator('.timeline-item').nth(1)).to_contain_text('长期有效 → 已停用')
    page.get_by_text('查看修改前后全文', exact=True).click()
    expect(page.locator('.timeline-item').first.locator('blockquote')).to_have_text(['共同开头旧内容共同结尾', '共同开头新内容共同结尾'])
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name='下一页', exact=True).click()
    expect(page.locator('.timeline-item')).to_have_count(2)
    expect(dialog.get_by_role('button', name='下一页', exact=True)).to_be_disabled()
    dialog.get_by_role('button', name='上一页', exact=True).click()
    expect(page.locator('.timeline-item')).to_have_count(10)


def test_workspace_memory_uses_server_identity_not_demo_scope(page):
    from test.packages.memory_app.test_memory import pick_option
    page.get_by_role('button', name='添加记忆', exact=True).click()
    page.get_by_role('textbox', name='记忆内容', exact=True).fill('工作区项目约定')
    pick_option(page, '记忆作用范围', '指定工作区')
    page.get_by_role('textbox', name='记忆工作区路径').fill('/projects/real-workspace')
    page.get_by_role('button', name='保存记忆', exact=True).click()
    expect(page.locator('.memory-detail-text')).to_have_text('工作区项目约定')
    body = next(body for method, path, body, _ in page.mem0_calls if method == 'POST')
    assert body['user_id'] == 'configured-user'
    assert body['run_id'] == 'workspace:1234567890abcdef'
    assert body['metadata']['workspace_path'] == '/projects/real-workspace'


def test_missing_agent_context_keeps_draft_without_writing_memory(page):
    import json
    def route(socket):
        socket.on_message(lambda raw: socket.send(json.dumps({'jsonrpc': '2.0', 'id': json.loads(raw)['id'], 'error': {'code': -32601, 'message': '请更新 Gateway'}})))
    page.route_web_socket('**/rpc', route)
    page.get_by_role('button', name='添加记忆', exact=True).click()
    page.get_by_role('textbox', name='记忆内容', exact=True).fill('保留待保存内容')
    expect(page.get_by_role('alert')).to_contain_text('请更新 Gateway')
    expect(page.get_by_role('button', name='保存记忆', exact=True)).to_be_disabled()
    assert not any(method == 'POST' for method, _, _, _ in page.mem0_calls)
    expect(page.get_by_role('textbox', name='记忆内容', exact=True)).to_have_value('保留待保存内容')


def test_legacy_scope_can_be_recreated_without_destroying_old_memory(page):
    original = page.mem0_rows['mem_a8f2c1']
    original['run_id'] = 'workspace:icarus'
    original['user_id'] = 'default'
    page.reload()
    page.locator('[data-memory-id="mem_a8f2c1"] .memory-entry-open').click()
    page.get_by_role('button', name='按当前配置重新添加').click()
    expect(page.get_by_role('textbox', name='记忆内容', exact=True)).to_have_value(original['memory'])
    page.get_by_role('button', name='保存记忆', exact=True).click()
    expect(page.locator('.memory-detail-text')).to_have_text(original['memory'])
    body = next(body for method, path, body, _ in page.mem0_calls if method == 'POST')
    assert body['user_id'] == 'configured-user'
    assert body['run_id'] == 'global'
    assert page.mem0_rows['mem_a8f2c1']['run_id'] == 'workspace:icarus'
    assert len(page.mem0_rows) == 9


def test_memory_paging_filter_sort_and_selection_scope(page):
    seed_paged_memories(page, 30)
    page.reload()
    size = memory_page_size(page)
    pages = math.ceil(38 / size)
    expect(page.locator('.memory-entry')).to_have_count(size)
    paging = page.get_by_role('navigation', name='记忆分页')
    expect(paging).to_contain_text(f'共 38 条 · 每页 {size} 条 · 第 1 / {pages} 页')
    page.get_by_role('button', name='多选', exact=True).click()
    page.get_by_text('全选当前结果', exact=True).click()
    expect(page.locator('.selection-count')).to_have_text(f'已选 {size} 条')
    paging.get_by_role('button', name='下一页').click()
    expect(paging).to_contain_text(f'第 2 / {pages} 页')
    expect(page.get_by_role('button', name='多选', exact=True)).to_be_visible()
    expect(page.locator('.memory-content').first).to_have_text(f'分页记忆 {29 - size}')
    search = page.get_by_role('textbox', name='搜索记忆…')
    search.fill('分页记忆 29')
    expect(page.locator('.memory-entry')).to_have_count(1)
    expect(paging).to_contain_text('共 1 条')
    expect(paging).to_contain_text('第 1 / 1 页')
    expect(page.locator('.memory-content')).to_have_text('分页记忆 29')
    search.fill('')
    size = memory_page_size(page)
    expect(page.locator('.memory-entry')).to_have_count(size)
    expect(paging).to_contain_text('第 1 /')
    page.get_by_role('button', name='更新时间：从新到旧', exact=True).click()
    expect(page.locator('.memory-content').first).to_have_text('本轮原型评审安排在九月第一周。')
    assert any('page=2' in url for method, path, _, url in page.mem0_calls if path == '/memories/page')


def test_fixed_page_size_preserves_page_across_sidebar_and_window_resize(page):
    seed_paged_memories(page)
    page.set_viewport_size({'width': 1280, 'height': 1080})
    page.reload()
    assert memory_page_size(page) == 15
    paging = page.get_by_role('navigation', name='记忆分页')
    paging.get_by_role('button', name='下一页').click()
    memory_page_size(page)
    ids = page.locator('.memory-entry').evaluate_all('cards => cards.map(card => card.dataset.memoryId)')
    requests = len(page.mem0_calls)
    for action in ['收起侧边栏', '展开侧边栏']:
        page.get_by_role('button', name=action, exact=True).click()
        assert memory_page_size(page) == 15
        assert page.locator('.memory-entry').evaluate_all('cards => cards.map(card => card.dataset.memoryId)') == ids
        expect(paging).to_contain_text('第 2 / 5 页')
    for width, height in [(1280, 600), (1920, 4000), (390, 844)]:
        page.set_viewport_size({'width': width, 'height': height})
        assert memory_page_size(page) == 15
        expect(page.locator('.memory-entry')).to_have_count(15)
        assert page.locator('.memory-entry').evaluate_all('cards => cards.map(card => card.dataset.memoryId)') == ids
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert len(page.mem0_calls) == requests


def test_resize_keeps_bulk_selection_and_edit_draft_stable(page):
    seed_paged_memories(page)
    page.set_viewport_size({'width': 1280, 'height': 1080})
    page.reload()
    original_size = memory_page_size(page)
    page.get_by_role('button', name='多选', exact=True).click()
    page.get_by_text('全选当前结果', exact=True).click()
    page.get_by_role('button', name='收起侧边栏', exact=True).click()
    assert memory_page_size(page) == original_size
    expect(page.locator('.selection-count')).to_have_text(f'已选 {original_size} 条')
    expect(page.locator('.memory-entry')).to_have_count(original_size)
    page.get_by_role('button', name='取消多选', exact=True).click()
    resized_size = memory_page_size(page)
    assert resized_size == original_size == 15

    page.locator('.memory-entry-open').first.click()
    detail = page.get_by_role('dialog')
    detail.get_by_role('button', name='修正内容', exact=True).click()
    editor = detail.get_by_role('textbox', name='修正记忆内容')
    editor.fill('缩放时仍保留的草稿')
    page.set_viewport_size({'width': 1920, 'height': 1400})
    assert memory_page_size(page) == resized_size
    expect(editor).to_have_value('缩放时仍保留的草稿')
    page.keyboard.press('Escape')
    detail.get_by_role('button', name='放弃修改', exact=True).click()
    detail.wait_for(state='hidden')
    assert memory_page_size(page) == resized_size


def test_sidebar_animates_width_without_replacing_cards_or_changing_columns(page):
    seed_paged_memories(page)
    page.set_viewport_size({'width': 1280, 'height': 1080})
    page.reload()
    assert memory_page_size(page) == 15
    page.emulate_media(reduced_motion='no-preference')
    page.evaluate("window.memoryOriginalCards = [...document.querySelectorAll('.memory-entry')]")
    requests = len(page.mem0_calls)
    original = page.locator('.memory-entry').first.bounding_box()
    columns = page.locator('.memory-list').evaluate(
        'element => getComputedStyle(element).gridTemplateColumns.split(" ").length')
    assert columns == 5
    report = page.evaluate('''async () => {
        let mutations = 0, minOpacity = 1, intermediateWidth = false;
        const observer = new MutationObserver(records => mutations += records.length);
        observer.observe(document.querySelector('.memory-list'), {childList: true});
        document.querySelector('[aria-label="收起侧边栏"]').click();
        const started = performance.now();
        await new Promise(resolve => {
            function sample() {
                const width = document.querySelector('.app-sidebar').getBoundingClientRect().width;
                intermediateWidth ||= width > 65 && width < 207;
                for (const card of window.memoryOriginalCards)
                    minOpacity = Math.min(minOpacity, Number(getComputedStyle(card).opacity));
                if (performance.now() - started < 400) requestAnimationFrame(sample); else resolve();
            }
            requestAnimationFrame(sample);
        });
        observer.disconnect();
        return {mutations, minOpacity, intermediateWidth,
            retained: window.memoryOriginalCards.every((card, index) =>
                card === document.querySelectorAll('.memory-entry')[index])};
    }''')
    assert report == {'mutations': 0, 'minOpacity': 1, 'intermediateWidth': True, 'retained': True}
    assert len(page.mem0_calls) == requests
    assert page.locator('.memory-list').evaluate(
        'element => getComputedStyle(element).gridTemplateColumns.split(" ").length') == columns
    current = page.locator('.memory-entry').first.bounding_box()
    assert current['width'] > original['width']
    assert abs(current['y'] - original['y']) <= 1
    expect(page.locator('.memory-board')).to_have_attribute('aria-busy', 'false')
    page.screenshot(path='test-results/memory-stable-collapsed.png', full_page=True)
    page.get_by_role('button', name='展开侧边栏', exact=True).click()
    memory_page_size(page)
    assert len(page.mem0_calls) == requests


def test_sidebar_resize_cancels_running_page_animation(page):
    seed_paged_memories(page)
    page.set_viewport_size({'width': 1280, 'height': 1080})
    page.reload()
    size = memory_page_size(page)
    page.emulate_media(reduced_motion='no-preference')
    page.add_style_tag(content='::view-transition-group(*) { animation-duration: 2s !important; }')
    page.evaluate('''() => {
        const start = document.startViewTransition.bind(document);
        window.memorySkippedTransitions = 0;
        document.startViewTransition = update => {
            const transition = start(update);
            const skip = transition.skipTransition.bind(transition);
            transition.skipTransition = () => {
                window.memorySkippedTransitions++;
                skip();
            };
            window.memoryTransition = transition;
            return transition;
        };
    }''')
    page.get_by_role('navigation', name='记忆分页').get_by_role('button', name='下一页').click()
    page.wait_for_function('window.memoryTransition !== undefined')
    page.evaluate('''async () => {
        await window.memoryTransition.ready;
        document.querySelector('[aria-label="收起侧边栏"]').click();
        await window.memoryTransition.finished;
    }''')
    assert page.evaluate('window.memorySkippedTransitions') == 1
    assert memory_page_size(page) == size == 15
    assert page.evaluate('''() => document.getAnimations().filter(animation =>
        animation.effect?.pseudoElement?.startsWith('::view-transition')
    ).length''') == 0


def test_agent_record_source_properties_and_body_priority(page):
    row = page.mem0_rows['mem_a8f2c1']
    row['metadata'].update(origin='explicit', source_session_id='agent-session',
                           source_run_id='execution-42', source_workspace_key='1234567890abcdef',
                           source_operation_id='operation-42')
    page.locator('[data-memory-id="mem_a8f2c1"] .memory-entry-open').click()
    detail = page.get_by_role('dialog')
    expect(detail).to_contain_text('Agent 记录')
    source = detail.locator('.memory-source')
    expect(source).not_to_have_attribute('open', '')
    expect(source.get_by_text('agent-session', exact=True)).not_to_be_visible()
    expect(source.get_by_text('execution-42', exact=True)).not_to_be_visible()
    properties = detail.locator('.memory-detail-properties').first.bounding_box()
    body = detail.locator('.memory-detail-text').bounding_box()
    assert body['y'] + body['height'] <= properties['y']
    source.locator('summary').click()
    expect(source.get_by_text('agent-session', exact=True)).to_be_visible()
    expect(source.get_by_text('execution-42', exact=True)).to_be_visible()
    page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copiedSource = text}}})")
    source.get_by_role('button', name='复制来源信息', exact=True).click()
    expect(page.get_by_text('已复制来源信息', exact=True)).to_be_visible()
    assert page.evaluate('window.copiedSource') == '来源会话：agent-session\n来源执行：execution-42\n来源工作区标识：1234567890abcdef\n来源操作标识：operation-42'
    detail.get_by_role('button', name='close', exact=True).click()
    page.locator('[data-memory-id="mem_a8f2c1"] .memory-entry-open').click()
    expect(detail.locator('.memory-source')).not_to_have_attribute('open', '')
