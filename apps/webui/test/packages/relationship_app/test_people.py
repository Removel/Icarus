from playwright.sync_api import expect


def test_people_search_pagination_and_archive(page, rela):
    cards = page.locator('.person-card')
    expect(cards).to_have_count(20)
    expect(page.get_by_text('本页 20 人', exact=True)).to_be_visible()
    page.get_by_role('button', name='下一页', exact=True).click()
    expect(cards).to_have_count(3)
    page.get_by_role('button', name='上一页', exact=True).click()
    expect(cards).to_have_count(20)
    page.get_by_role('textbox', name='搜索姓名或别名').fill('王明')
    expect(cards).to_have_count(2)
    assert cards.nth(0).get_attribute('href') != cards.nth(1).get_attribute('href')
    assert rela['calls'][-1][2].get('cursor') is None
    page.get_by_role('textbox', name='搜索姓名或别名').fill('归档人物')
    expect(page.get_by_text('没有匹配的人物', exact=True)).to_be_visible()
    page.get_by_text('包含已归档', exact=True).click()
    expect(cards).to_have_count(1)
    expect(cards.first.get_by_text('人物', exact=True)).to_be_visible()
    expect(cards.first.get_by_text('已归档', exact=True)).to_be_visible()
    cards.first.click()
    expect(page.get_by_role('dialog', name='人物档案', exact=True)).to_be_visible()
    expect(page.get_by_role('heading', name='归档人物', exact=True)).to_be_visible()
    assert any(path == '/v1/persons/p_archived' and q.get('include_archived') == 'true' for _, path, q, _ in rela['calls'])


def test_people_offline_keeps_shell_and_retry(page, rela):
    expect(page.locator('.person-card')).to_have_count(20)
    rela['fail']['/v1/persons'] = 503
    page.get_by_role('button', name='刷新人物', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('不可用')
    expect(page.get_by_role('link', name='返回对话', exact=True)).to_be_visible()
    rela['fail'].clear()
    page.get_by_role('button', name='重试', exact=True).click()
    expect(page.get_by_role('alert')).to_have_count(0)


def test_account_menu_owns_management_destinations(page, rela):
    expect(page.locator('.person-card')).to_have_count(20)
    expect(page.locator('.chat-management-links')).to_have_count(0)
    page.get_by_role('button', name='账户菜单', exact=True).click()
    menu = page.get_by_role('menu')
    for label in ('知识库', '记忆', '人物', '打开设置'):
        expect(menu.get_by_role('menuitem', name=label, exact=True)).to_be_visible()


def test_cursor_survives_reload_categories_and_detail_close(page, rela):
    cards = page.locator('.person-card')
    expect(cards).to_have_count(20)
    page.get_by_role('button', name='下一页', exact=True).click()
    expect(cards).to_have_count(3)
    page.reload()
    expect(cards).to_have_count(3)
    tabs = page.get_by_role('navigation', name='人物分类')
    tabs.get_by_role('link', name='圈子', exact=True).click()
    expect(page.locator('.rela-circle-row')).to_have_count(1)
    tabs.get_by_role('link', name='人物', exact=True).click()
    expect(cards).to_have_count(3)
    cards.first.click()
    page.get_by_role('button', name='关闭人物档案', exact=True).click()
    expect(cards).to_have_count(3)
    expect(cards.first).to_be_focused()
    page.get_by_role('button', name='上一页', exact=True).click()
    expect(cards).to_have_count(20)


def test_stale_search_cannot_replace_new_result_and_invalid_cursor_recovers(page, rela):
    pending = []
    def delay(route):
        if 'query=stale' in route.request.url:
            pending.append(route)
        else:
            route.fallback()
    page.route('**/api/rela/v1/persons?**', delay)
    search = page.get_by_role('textbox', name='搜索姓名或别名')
    search.fill('stale')
    page.wait_for_function("location.hash.includes('stale')")
    search.fill('小王')
    expect(page.locator('.person-card')).to_have_count(1)
    assert pending
    pending[0].fulfill(json={'items': [rela['people'][1]], 'has_more': False, 'next_cursor': None})
    expect(page.locator('.person-card').first).to_have_attribute('data-person-id', 'p_000001_alpha')
    page.goto(page.url.split('#')[0] + '#/people?cursor=invalid')
    expect(page.get_by_role('alert')).to_contain_text('游标无效')
    page.get_by_role('button', name='回到首屏', exact=True).click()
    expect(page.locator('.person-card')).to_have_count(20)
