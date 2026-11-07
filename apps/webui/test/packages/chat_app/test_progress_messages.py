import pytest
from playwright.sync_api import expect

from test.navigation import open_process
from test.packages.chat_app.test_chat import gateway


@pytest.mark.parametrize('width', [390, 1440])
def test_tool_preambles_never_become_answer_bubbles(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    _, emit = gateway(page)
    emit('user.message', {'text': '查一下记忆数据库'})
    emit('task.started', {})
    for step, text in [(1, '我先查一下。'), (2, '我再查一层。')]:
        emit('assistant.text_delta', {'step': step, 'text': text})
        expect(page.locator('.chat-progress-text').last).to_have_text(text)
        expect(page.locator('.chat-assistant')).to_have_count(0)
        emit('assistant.message', {'step': step, 'text': text, 'has_tool_calls': True})
        # A completed model response is not necessarily the task's answer.
        expect(page.get_by_role('button', name='复制回答', exact=True)).to_have_count(0)
        emit('tool.started', {'step': step, 'call_id': f'call-{step}',
                              'tool_name': 'read', 'arguments': {}})
        emit('tool.completed', {'step': step, 'call_id': f'call-{step}',
                                'tool_name': 'read', 'success': True, 'output_preview': 'ok'})
    emit('assistant.text_delta', {'step': 3, 'text': '当前连接的是同一个数据库。'})
    expect(page.locator('.chat-progress-text').last).to_have_text('当前连接的是同一个数据库。')
    emit('assistant.message', {'step': 3, 'text': '当前连接的是同一个数据库。', 'has_tool_calls': False})
    emit('task.finished', {'status': 'completed'})
    expect(page.locator('.chat-assistant')).to_have_text('Icarus当前连接的是同一个数据库。')
    expect(page.locator('.assistant-actions')).to_have_count(1)
    expect(page.locator('.chat-process .assistant-actions')).to_have_count(0)
    page.screenshot(path=f'test-results/progress-messages-{width}.png')
    page.reload()
    expect(page.locator('.chat-assistant')).to_contain_text('当前连接的是同一个数据库。')
    expect(page.locator('.chat-assistant')).to_have_count(1)
    open_process(page)
    expect(page.locator('.chat-progress-text')).to_have_text(['我先查一下。', '我再查一层。'])


def test_old_history_uses_tool_steps_without_text_matching(page):
    state, emit = gateway(page)
    emit('user.message', {'text': '核对状态'})
    emit('assistant.message', {'step': 1, 'text': 'A completely arbitrary preamble'})
    emit('tool.completed', {'step': 1, 'call_id': 'read', 'tool_name': 'read',
                            'success': False, 'error': 'unavailable'})
    emit('assistant.message', {'step': 2, 'text': '我先查一下是用户原文，不应按关键词删除。'})
    emit('task.finished', {'status': 'completed'})
    page.reload()
    expect(page.locator('.chat-assistant')).to_have_count(1)
    expect(page.locator('.chat-assistant')).to_contain_text('不应按关键词删除')
    open_process(page)
    expect(page.locator('.chat-progress-text')).to_have_text('A completely arbitrary preamble')
    assert state['records'][1]['payload']['text'] == 'A completely arbitrary preamble'


@pytest.mark.parametrize('status', ['cancelled', 'failed'])
def test_partial_response_is_preserved_when_task_stops(page, status):
    _, emit = gateway(page)
    emit('task.started', {})
    emit('assistant.text_delta', {'step': 1, 'text': '已经取得的部分结果'})
    expect(page.locator('.chat-assistant')).to_have_count(0)
    emit('task.finished', {'status': status})
    expect(page.locator('.chat-assistant')).to_contain_text('已经取得的部分结果')


def test_confirmed_tool_preamble_stays_in_process_if_tool_never_starts(page):
    _, emit = gateway(page)
    emit('task.started', {})
    emit('assistant.message', {'step': 1, 'text': '准备检查', 'has_tool_calls': True})
    emit('task.finished', {'status': 'cancelled'})
    expect(page.locator('.chat-assistant')).to_have_count(0)
    expect(page.locator('.chat-progress-text')).to_have_text('准备检查')


def test_later_step_reclassifies_an_earlier_candidate_without_crossing_tasks(page):
    _, emit = gateway(page)
    emit('assistant.message', {'step': 1, 'text': '另一个任务的答案'}, task_id='other')
    emit('task.started', {})
    emit('assistant.message', {'step': 1, 'text': '应用引导前的阶段结果', 'has_tool_calls': False})
    emit('assistant.thinking_delta', {'step': 2, 'text': '处理新的引导'})
    expect(page.locator('.chat-progress-text')).to_have_text('应用引导前的阶段结果')
    emit('assistant.message', {'step': 2, 'text': '应用引导后的答案', 'has_tool_calls': False})
    emit('task.finished', {'status': 'completed'})
    expect(page.locator('.chat-assistant')).to_have_count(2)
    expect(page.locator('.chat-assistant').first).to_contain_text('另一个任务的答案')
    expect(page.locator('.chat-assistant').last).to_contain_text('应用引导后的答案')


def test_collapsed_process_stays_closed_and_final_answer_keeps_sources(page):
    _, emit = gateway(page)
    emit('task.started', {})
    emit('assistant.text_delta', {'step': 1, 'text': '准备核对来源'})
    expect(page.locator('.chat-progress-text')).to_be_visible()
    page.locator('.chat-process > summary').click()
    emit('assistant.text_delta', {'step': 1, 'text': '，继续核对'})
    emit('assistant.message', {'step': 1, 'text': '准备核对来源，继续核对', 'has_tool_calls': True})
    expect(page.locator('.chat-process')).not_to_have_attribute('open', '')
    emit('web_search.updated', {'status': 'complete', 'total': 1, 'queries': ['test'],
         'sources': [{'citeIndex': 1, 'title': '官方资料', 'url': 'https://example.com'}],
         'pages': [], 'browsing': False, 'notice': ''})
    emit('assistant.message', {'step': 2, 'text': '查到的结论 [1]', 'has_tool_calls': False})
    emit('task.finished', {'status': 'completed'})
    expect(page.locator('.chat-process')).not_to_have_attribute('open', '')
    page.get_by_role('button', name='查看来源 1', exact=True).click()
    expect(page.get_by_role('dialog', name='联网来源', exact=True)).to_contain_text('官方资料')


def test_legacy_cached_preambles_do_not_flash_before_history_revalidation(page):
    state, emit = gateway(page)
    emit('assistant.message', {'step': 1, 'text': '旧缓存中的阶段性说明'})
    expect(page.locator('.chat-assistant')).to_be_visible()
    # Simulate the cache saved before step/tool metadata existed in ChatItem.
    page.wait_for_function('''() => new Promise(resolve => {
      const open = indexedDB.open('icarus.webui.history', 1);
      open.onsuccess = () => {
        const db = open.result;
        const tx = db.transaction('sessions', 'readwrite');
        const cursor = tx.objectStore('sessions').openCursor();
        let changed = false;
        cursor.onsuccess = () => {
          const row = cursor.result;
          if (!row) return;
          const value = row.value;
          delete value.formatVersion;
          value.transcript.items.forEach(item => { delete item.step; delete item.hasToolCalls; });
          row.update(value);
          changed = true;
          row.continue();
        };
        tx.oncomplete = () => { db.close(); resolve(changed); };
      };
    })''')
    state['hold_history'] = True
    page.expose_function('pending_history_count', lambda: len(state.get('pending_history', [])))
    page.reload()
    page.wait_for_function('async () => await window.pending_history_count() > 0')
    expect(page.locator('.chat-assistant')).to_have_count(0)
