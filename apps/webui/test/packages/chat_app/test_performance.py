import json

import pytest
from playwright.sync_api import expect
from test.packages.chat_app.test_chat import gateway
from test.navigation import delete_session


def test_stream_keeps_completed_markdown_nodes_unchanged(page):
    _, emit = gateway(page)
    emit('assistant.text_delta', {'step': 1, 'text': '# 固定标题\n\n固定段落\n\n正在输出'})
    expect(page.locator('.chat-markdown h1')).to_have_text('固定标题')
    page.evaluate('''() => {
        window.stableHeading = document.querySelector('.chat-markdown h1');
        window.stableParagraph = document.querySelector('.chat-markdown p');
        window.stableMutations = [];
        window.stableObserver = new MutationObserver(entries => window.stableMutations.push(...entries));
        for (const node of [window.stableHeading, window.stableParagraph])
            window.stableObserver.observe(node, {subtree: true, childList: true, characterData: true});
    }''')
    for text in ['，新增内容', '\n\n又一段落', '继续输出']:
        emit('assistant.text_delta', {'step': 1, 'text': text})
    expect(page.locator('.chat-markdown')).to_contain_text('继续输出')
    assert page.evaluate('''() => window.stableMutations.length === 0
        && document.querySelector('.chat-markdown h1') === window.stableHeading
        && document.querySelector('.chat-markdown p') === window.stableParagraph''')
    emit('assistant.message', {'step': 1, 'text': '# 更正标题\n\n最终正文'})
    expect(page.locator('.chat-markdown h1')).to_have_text('更正标题')
    expect(page.locator('.chat-markdown p')).to_have_text('最终正文')


def test_incomplete_math_and_table_stay_readable_until_complete(page):
    _, emit = gateway(page)
    emit('assistant.text_delta', {'step': 1, 'text': '$$\n\\frac{'})
    expect(page.locator('.chat-markdown-tail')).to_contain_text('\\frac{')
    expect(page.locator('.katex-error')).to_have_count(0)
    emit('assistant.text_delta', {'step': 1, 'text': '1}{2}\n$$\n\n| 列 |'})
    expect(page.locator('.katex')).to_be_visible()
    expect(page.locator('.chat-markdown-tail')).to_have_text('| 列 |')
    emit('assistant.text_delta', {'step': 1, 'text': '\n| --- |\n| 值 |'})
    expect(page.locator('.chat-markdown td')).to_have_text('值')


def test_late_reference_definitions_keep_document_context(page):
    _, emit = gateway(page)
    emit('assistant.text_delta', {'step': 1, 'text': '[来源][ref]\n\n第二段\n\n尾部'})
    emit('assistant.text_delta', {'step': 1, 'text': '\n\n[ref]: https://example.com/reference'})
    expect(page.get_by_role('link', name='来源', exact=True)).to_have_attribute('href', 'https://example.com/reference')


def test_streaming_code_defers_highlighting_until_final(page):
    _, emit = gateway(page)
    text = '```python\nprint("hello")\n'
    emit('assistant.text_delta', {'step': 1, 'text': text})
    expect(page.locator('.chat-code-block pre code')).to_contain_text('print("hello")')
    expect(page.locator('.chat-code-block .semi-codeHighlight')).to_have_count(0)
    emit('assistant.message', {'step': 1, 'text': text + '```'})
    expect(page.locator('.chat-code-block .semi-codeHighlight')).to_be_visible()
    expect(page.locator('.chat-code-block .token').first).to_be_visible()


def test_indexeddb_cache_paints_before_history_and_revalidates_from_zero(page):
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
                'type': 'assistant.message', 'payload': {'step': 1, 'text': '本地缓存回答'}, 'sequence': 1}]
    state, _ = gateway(page, records=records)
    expect(page.locator('.chat-assistant')).to_contain_text('本地缓存回答')
    page.wait_for_function('''() => new Promise(resolve => {
        const open = indexedDB.open('icarus.webui.history', 1);
        open.onsuccess = () => { const db = open.result;
            const read = db.transaction('sessions').objectStore('sessions').count();
            read.onsuccess = () => { resolve(read.result > 0); db.close(); };
        };
    })''')
    state['hold_history'] = True
    page.reload()
    expect(page.locator('.chat-assistant')).to_contain_text('本地缓存回答')
    expect(page.get_by_role('textbox', name='发送消息')).to_be_disabled()
    reads = [call for call in state['calls'] if call['method'] == 'session.get_history']
    assert reads[-1]['params']['after_sequence'] == 0
    pending = state['pending_history'][-1]
    revised = {**records[0], 'payload': {'step': 1, 'text': '服务端校准回答'}}
    pending[0].send(json.dumps({'jsonrpc': '2.0', 'id': pending[1], 'result': {
        'records': [revised], 'has_more': False, 'next_after_sequence': 1, 'history_cursor': 1}}))
    expect(page.locator('.chat-assistant')).to_contain_text('服务端校准回答')
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    assert page.evaluate("performance.getEntriesByName('icarus.chat.history').length") == 1


def test_stalled_indexeddb_never_blocks_rpc_hydration_and_circuit_breaks(page):
    page.add_init_script('''(() => {
        window.cacheOpens = 0;
        IDBFactory.prototype.open = function() { window.cacheOpens++; return {}; };
        const timeout = window.setTimeout.bind(window);
        window.setTimeout = (fn, delay, ...args) => timeout(fn, delay === 3000 ? 10 : delay, ...args);
    })()''')
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
                'type': 'assistant.message', 'payload': {'step': 1, 'text': '缓存失效仍可读取'}, 'sequence': 1}]
    gateway(page, records=records, sessions=[
        {'session_id': 'session-1', 'title': '第一段', 'first_user_input': ''},
        {'session_id': 'session-2', 'title': '第二段', 'first_user_input': ''}])
    expect(page.locator('.chat-assistant')).to_contain_text('缓存失效仍可读取')
    # The debounced write provides a third timeout, then the cache is disabled.
    page.wait_for_function('window.cacheOpens >= 3')
    page.wait_for_function("performance.now() > 1000")
    attempts = page.evaluate('window.cacheOpens')
    page.get_by_role('button', name='第二段', exact=True).click()
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled()
    page.get_by_role('button', name='第一段', exact=True).click()
    expect(page.locator('.chat-assistant')).to_contain_text('缓存失效仍可读取')
    assert page.evaluate('window.cacheOpens') == attempts


def test_deleted_session_does_not_return_to_persistent_cache_on_cleanup(page):
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
                'type': 'assistant.message', 'payload': {'step': 1, 'text': '即将删除的缓存'}, 'sequence': 1}]
    gateway(page, records=records, sessions=[{'session_id': 'session-1', 'title': '待删除', 'first_user_input': ''}])
    count = '''() => new Promise(resolve => {
        const open = indexedDB.open('icarus.webui.history', 1);
        open.onsuccess = () => { const db = open.result;
            const read = db.transaction('sessions').objectStore('sessions').count();
            read.onsuccess = () => { resolve(read.result); db.close(); };
        };
    })'''
    page.wait_for_function('async () => (' + count + ')() .then(count => count > 0)')
    delete_session(page, '待删除')
    page.get_by_role('dialog', name='删除这段对话？', exact=True).get_by_role('button', name='删除', exact=True).click()
    expect(page.locator('.chat-assistant')).to_have_count(0)
    page.wait_for_function('async () => (' + count + ')() .then(count => count === 0)')


def test_enabling_virtualization_preserves_the_message_being_read(page):
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'task-1',
                'type': 'assistant.message', 'payload': {'step': index, 'text': f'消息 {index} ' + '正文' * (5 + index % 13)},
                'sequence': index} for index in range(1, 80)]
    _, emit = gateway(page, records=records)
    expect(page.locator('.chat-assistant')).to_have_count(79)
    page.locator('.chat-transcript').evaluate('el => el.scrollTop = 1500')
    expect(page.get_by_role('button', name='回到最新消息')).to_be_visible()
    before = page.evaluate('''() => {
        const top = document.querySelector('.chat-transcript').getBoundingClientRect().top;
        const row = Array.from(document.querySelectorAll('.chat-assistant')).find(node => node.getBoundingClientRect().bottom > top);
        return {text: row.textContent, top: row.getBoundingClientRect().top};
    }''')
    emit('assistant.message', {'step': 80, 'text': '第八十条消息'})
    expect(page.locator('[data-virtualized]')).to_have_count(1)
    page.wait_for_function('''expected => {
        const row = Array.from(document.querySelectorAll('.chat-assistant')).find(node => node.textContent === expected.text);
        return row && Math.abs(row.getBoundingClientRect().top - expected.top) < 2;
    }''', arg=before)


@pytest.mark.parametrize('width', [390, 1440])
def test_long_transcript_bounds_dom_and_can_jump_to_unmounted_messages(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    records = []
    for turn in range(120):
        for kind, text in [('user.message', f'问题 {turn}'), ('assistant.message', f'回答 {turn}\n\n' + '不同高度的正文。' * (10 + turn % 11))]:
            records.append({'workspace_key': 'workspace-key', 'session_id': 'session-1',
                            'task_id': f'task-{turn}', 'type': kind,
                            'payload': {'text': text, 'step': 1}, 'sequence': len(records) + 1})
    _, emit = gateway(page, records=records)
    expect(page.locator('[data-virtualized]')).to_have_count(1)
    expect(page.locator('.chat-assistant').last).to_contain_text('回答 119')
    assert 0 < page.locator('.chat-virtual-row').count() < 40
    if width < 768:
        page.set_viewport_size({'width': 1024, 'height': 900})
    page.locator('.scroll-nav-item[title="问题 20"]').click()
    expect(page.locator('.chat-user').filter(has_text='问题 20')).to_be_in_viewport()
    assert page.evaluate('document.activeElement.classList.contains("chat-user")')
    log = page.locator('.chat-transcript')
    before = log.evaluate('el => el.scrollTop')
    emit('assistant.text_delta', {'step': 1, 'text': '新输出' * 100}, task_id='task-new')
    expect(page.get_by_role('button', name='回到最新消息')).to_be_visible()
    assert abs(log.evaluate('el => el.scrollTop') - before) < 2
    page.get_by_role('button', name='回到最新消息').click()
    expect(page.locator('.chat-assistant').last).to_contain_text('新输出')
    page.wait_for_function('''() => { const el = document.querySelector('.chat-transcript');
        return el.scrollHeight - el.clientHeight - el.scrollTop < 2; }''')
    page.set_viewport_size({'width': 768, 'height': 800})
    expect(page.locator('.chat-assistant').last).to_contain_text('新输出')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
