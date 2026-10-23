from playwright.sync_api import expect


def open_navigation(page):
    if page.viewport_size['width'] < 768:
        trigger = page.get_by_role('button', name='打开会话列表', exact=True)
        expect(trigger).to_be_visible()
        if trigger.get_attribute('aria-expanded') != 'true':
            trigger.click()
        expect(page.get_by_role('complementary', name='会话管理', exact=True)).to_be_visible()


def follow_link(page, name):
    # Lazy chat may not have mounted immediately after a route change.
    if page.url.split('#')[-1].startswith('/chat'):
        expect(page.get_by_role('region', name='Agent 对话')).to_be_visible()
    if name == '对话' and page.get_by_role('link', name='返回对话', exact=True).is_visible():
        page.get_by_role('link', name='返回对话', exact=True).click()
        return
    else:
        open_navigation(page)
        trigger = page.get_by_role('button', name='展开会话列表', exact=True)
        if name in ('记忆', '知识库') and trigger.is_visible():
            trigger.click()
    page.get_by_role('link', name=name, exact=True).click()


def open_sessions(page):
    if page.viewport_size['width'] < 768:
        trigger = page.get_by_role('button', name='打开会话列表', exact=True)
        expect(trigger).to_be_visible()
        if trigger.get_attribute('aria-expanded') != 'true':
            trigger.click()
        expect(page.get_by_role('complementary', name='会话管理', exact=True)).to_be_visible()


def open_settings(page):
    trigger = page.get_by_role('button', name='账户菜单', exact=True)
    if page.viewport_size['width'] < 768:
        open_sessions(page)
    elif not trigger.is_visible():
        name = '打开会话列表' if page.viewport_size['width'] < 768 else '展开会话列表'
        page.get_by_role('button', name=name, exact=True).click()
    trigger.click()
    page.get_by_role('menuitem', name='打开设置', exact=True).click()
    expect(page.get_by_role('dialog', name='设置', exact=True)).to_be_visible()


def delete_session(page, title):
    page.get_by_role('button', name=f'会话操作：{title}', exact=True).click()
    page.get_by_role('menuitem', name=f'删除会话：{title}', exact=True).click()
