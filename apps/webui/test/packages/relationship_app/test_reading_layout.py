import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('circle', [False, True])
def test_empty_reading_aligns_identity_and_content_without_pagination(page, rela, width, circle):
    rela['profiles'] = []
    page.set_viewport_size({'width': width, 'height': 900})
    if circle:
        page.get_by_role('navigation', name='人物分类').get_by_role('link', name='圈子', exact=True).click()
        page.locator('.rela-circle-row').first.click()
    else:
        page.locator('.person-card').first.click()
    dialog = page.get_by_role('dialog', name='圈子详情' if circle else '人物档案', exact=True)
    expect(dialog.get_by_text('暂无成员' if circle else '还没有稳定档案', exact=True)).to_be_visible()
    expect(dialog.get_by_role('navigation', name='分页', exact=True)).to_have_count(0)
    expect(dialog.get_by_role('heading', name='稳定档案', exact=True)).to_have_count(0)
    expect(dialog.get_by_text('暂无别名', exact=True)).to_have_count(0)
    left = dialog.locator('.rela-detail-top > span').bounding_box()['x']
    for selector in ('.rela-identity-heading > .rela-avatar', '.rela-detail-content > :first-child'):
        assert abs(dialog.locator(selector).bounding_box()['x'] - left) <= 1
    if not circle:
        assert abs(dialog.get_by_role('tab', name='稳定档案', exact=True).bounding_box()['x'] - left) <= 1
        toolbar = dialog.locator('.rela-profile-toolbar')
        search = toolbar.get_by_role('textbox', name='搜索档案 key 或内容', exact=True)
        add = toolbar.get_by_role('button', name='添加档案', exact=True)
        assert abs(search.bounding_box()['y'] - add.bounding_box()['y']) <= 1
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth')
    # Exercise the entire ID pill without touching the system clipboard.
    page.evaluate("navigator.clipboard.writeText = async value => { window.copiedIdentity = value; }")
    label = 'Circle ID' if circle else 'Person ID'
    dialog.get_by_role('button', name=f'复制完整 {label}', exact=True).click()
    assert page.evaluate('window.copiedIdentity') == ('c_work' if circle else 'p_000001_alpha')
    action = '添加成员' if circle else '添加档案'
    dialog.get_by_role('button', name=action, exact=True).click()
    expect(page.get_by_role('dialog', name='添加成员' if circle else '添加档案字段', exact=True)).to_be_visible()


def test_empty_last_list_page_retains_previous_navigation(page, rela):
    expect(page.locator('.person-card')).to_have_count(20)
    # The last records disappear after the first page was read.
    del rela['people'][20:]
    page.get_by_role('button', name='下一页', exact=True).click()
    expect(page.locator('.person-card')).to_have_count(0)
    expect(page.get_by_role('button', name='上一页', exact=True)).to_be_enabled()
    page.get_by_role('button', name='上一页', exact=True).click()
    expect(page.locator('.person-card')).to_have_count(19)
    expect(page.get_by_role('navigation', name='分页', exact=True)).to_have_count(0)
