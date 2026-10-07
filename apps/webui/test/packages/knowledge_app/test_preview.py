import pytest
from playwright.sync_api import expect
from test_knowledge import fake_openkb, open_knowledge


@pytest.mark.parametrize('width', [1440, 390])
def test_live_browsing_uses_service_without_demo_controls(page, width):
    calls = fake_openkb(page)
    page.set_viewport_size({'width': width, 'height': 900})
    open_knowledge(page)
    expect(page.locator('.knowledge-card')).to_have_count(4)
    expect(page.get_by_role('button', name='新建知识库', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='刷新知识库', exact=True)).to_be_visible()
    expect(page.get_by_text('浏览示例', exact=True)).to_have_count(0)
    page.get_by_placeholder('搜索标题与摘要…').fill('Agent')
    expect(page.locator('.knowledge-card')).to_have_count(1)
    page.get_by_role('button', name='阅读知识：Agent 约束', exact=True).click()
    expect(page.get_by_role('button', name='编辑页面', exact=True)).to_be_enabled()
    page.reload()
    expect(page.get_by_role('heading', name='Agent 约束', exact=True)).to_be_visible()
    assert any(path == 'page' for path, _ in calls)
    assert page.locator('.shell-main').evaluate('el => el.scrollWidth <= el.clientWidth')


def test_knowledge_create_and_refresh_are_direct_actions(page):
    calls = fake_openkb(page)
    open_knowledge(page)
    count = len([path for path, _ in calls if path == 'list'])
    page.get_by_role('button', name='刷新知识库', exact=True).click()
    page.wait_for_function("document.querySelector('.knowledge-background [aria-label=\"正在加载知识库\"]') === null")
    assert len([path for path, _ in calls if path == 'list']) > count
    page.get_by_role('button', name='新建知识库', exact=True).click()
    expect(page.get_by_role('dialog', name='新建知识库', exact=True)).to_be_visible()
