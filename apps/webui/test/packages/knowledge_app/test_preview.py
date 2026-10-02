import os
import re

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize('width', [1440, 390])
def test_preview_browsing_without_service(page, width):
    requests = []
    page.route('**/api/v1/**', lambda route: (requests.append(route.request.url), route.abort()))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(os.environ.get('WEBUI_BASE_URL', 'http://127.0.0.1:5173') + '/#/knowledge/pages?preview=1')
    expect(page.locator('.knowledge-card')).to_have_count(8)
    assert page.get_by_role('button', name='导入资料', exact=True).count() == 0
    page.get_by_placeholder('搜索标题与摘要…').fill('无状态')
    expect(page.locator('.knowledge-card')).to_have_count(1)
    page.get_by_role('button', name='阅读知识：无状态 Agent', exact=True).click()
    expect(page.locator('.reading table')).to_be_visible()
    assert page.get_by_role('button', name='编辑页面', exact=True).is_disabled()
    page.locator('.reading').get_by_role('button', name='Plugin Runtime', exact=True).click()
    expect(page.get_by_role('heading', name='Plugin Runtime', exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role('heading', name='Plugin Runtime', exact=True)).to_be_visible()
    page.locator('.reading-references').get_by_role('button').click()
    expect(page.get_by_role('region', name='资料详情')).to_be_visible()
    page.get_by_role('button', name='资料操作', exact=True).click()
    assert page.get_by_role('menuitem', name='重新生成知识', exact=True).get_attribute('aria-disabled') == 'true'
    assert page.get_by_role('menuitem', name='移除资料', exact=True).get_attribute('aria-disabled') == 'true'
    page.keyboard.press('Escape')
    page.get_by_role('button', name='查看关联', exact=True).click()
    page.get_by_role('dialog', name='文档关联', exact=True).get_by_role('button', name='close', exact=True).click()
    expect(page.get_by_role('combobox', name='选择知识库')).to_have_count(0)
    page.get_by_role('link', name='知识页面', exact=True).click()
    page.get_by_role('button', name='阅读知识：无状态 Agent', exact=True).click()
    expect(page.get_by_role('heading', name='无状态 Agent', exact=True)).to_be_visible()
    assert page.locator('.shell-main').evaluate('el => el.scrollWidth <= el.clientWidth')
    page.get_by_role('dialog').get_by_role('button', name='close', exact=True).click()
    page.get_by_role('link', name='原始资料', exact=True).click()
    expect(page.locator('.source-card')).to_have_count(4)
    assert page.locator('.shell-main').evaluate('el => el.scrollWidth <= el.clientWidth')
    assert not requests


def test_preview_entry_and_return_to_live_service(page):
    page.route('**/api/v1/kbs', lambda route: route.fulfill(json={'knowledge_bases': []}))
    page.get_by_role('link', name='知识库', exact=True).click()
    expect(page.get_by_text('还没有知识库', exact=True)).to_be_visible()
    page.get_by_role('button', name='知识库设置', exact=True).click()
    page.get_by_role('menuitem', name='浏览示例', exact=True).click()
    expect(page.locator('.source-card')).to_have_count(4)
    page.get_by_role('button', name='知识库设置', exact=True).click()
    page.get_by_role('menuitem', name='返回真实知识库', exact=True).click()
    expect(page.get_by_text('还没有知识库', exact=True)).to_be_visible()
    expect(page.locator('.knowledge-card')).to_have_count(0)
