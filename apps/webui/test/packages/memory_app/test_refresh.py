from playwright.sync_api import expect


def add_external(page):
    row = dict(next(iter(page.mem0_rows.values())), id='external-memory',
               memory='外部写入的新记忆', updated_at='2099-01-01T00:00:00Z')
    page.mem0_rows[row['id']] = row


def test_focus_refreshes_external_memory_without_losing_editor(page):
    page.locator('.memory-entry-open').first.click()
    page.get_by_role('button', name='修正内容', exact=True).click()
    editor = page.get_by_role('textbox', name='修正记忆内容', exact=True)
    editor.fill('尚未保存的草稿')
    add_external(page)
    page.evaluate('window.dispatchEvent(new Event("focus"))')
    expect(page.locator('.memory-content', has_text='外部写入的新记忆')).to_have_count(1)
    expect(editor).to_have_value('尚未保存的草稿')


def test_manual_refresh_shows_external_memory(page):
    add_external(page)
    page.get_by_role('button', name='刷新记忆', exact=True).click()
    expect(page.locator('.memory-content', has_text='外部写入的新记忆')).to_have_count(1)


def test_visible_page_polls_and_hidden_page_pauses(page):
    page.clock.install()
    page.evaluate('''() => {
      Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
    }''')
    calls = len(page.mem0_calls)
    add_external(page)
    page.clock.run_for(30000)
    assert len(page.mem0_calls) == calls
    page.evaluate('''() => {
      Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
      document.dispatchEvent(new Event('visibilitychange'));
    }''')
    expect(page.locator('.memory-content', has_text='外部写入的新记忆')).to_have_count(1)
    page.mem0_rows['external-memory']['memory'] = '下一轮外部更新'
    page.clock.run_for(15000)
    expect(page.locator('.memory-content', has_text='下一轮外部更新')).to_have_count(1)


def test_focus_triggers_coalesce_and_refresh_failure_keeps_old_rows(page):
    pending = []
    page.route('**/api/mem0/memories/page?**', lambda route: pending.append(route))
    page.evaluate('window.dispatchEvent(new Event("focus"))')
    page.wait_for_timeout(50)
    assert len(pending) == 1
    page.evaluate('''() => {
      window.dispatchEvent(new Event('focus'));
      window.dispatchEvent(new Event('focus'));
    }''')
    page.wait_for_timeout(50)
    assert len(pending) == 1
    pending[0].fulfill(status=503, json={'detail': '服务暂不可用'})
    expect(page.get_by_role('alert')).to_be_visible()
    expect(page.locator('.memory-entry')).to_have_count(8)
    expect(page.get_by_text('还没有记忆', exact=True)).to_have_count(0)
