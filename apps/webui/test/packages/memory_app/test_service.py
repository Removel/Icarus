from playwright.sync_api import expect


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
    template = page.mem0_rows['mem_a8f2c1']
    for index in range(30):
        row = dict(template, id=f'page-{index}', memory=f'分页记忆 {index}',
                   updated_at=f'2026-10-02T00:{index:02}:00Z')
        page.mem0_rows[row['id']] = row
    page.reload()
    expect(page.locator('.memory-entry')).to_have_count(12)
    paging = page.get_by_role('navigation', name='记忆分页')
    expect(paging).to_contain_text('共 38 条 · 第 1 / 4 页')
    page.get_by_role('button', name='多选', exact=True).click()
    page.get_by_text('全选当前结果', exact=True).click()
    expect(page.locator('.selection-count')).to_have_text('已选 12 条')
    paging.get_by_role('button', name='下一页').click()
    expect(paging).to_contain_text('第 2 / 4 页')
    expect(page.get_by_role('button', name='多选', exact=True)).to_be_visible()
    expect(page.locator('.memory-content').first).to_have_text('分页记忆 17')
    search = page.get_by_role('textbox', name='搜索记忆…')
    search.fill('分页记忆 29')
    expect(page.locator('.memory-entry')).to_have_count(1)
    expect(paging).to_contain_text('共 1 条 · 第 1 / 1 页')
    expect(page.locator('.memory-content')).to_have_text('分页记忆 29')
    search.fill('')
    expect(page.locator('.memory-entry')).to_have_count(12)
    page.get_by_role('button', name='更新时间：从新到旧', exact=True).click()
    expect(page.locator('.memory-content').first).to_have_text('本轮原型评审安排在九月第一周。')
    assert any('page=2' in url for method, path, _, url in page.mem0_calls if path == '/memories/page')


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
