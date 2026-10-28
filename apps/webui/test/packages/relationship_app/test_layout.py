import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize('width,columns', [(1440, 3), (1024, 2), (768, 1), (390, 1)])
def test_card_columns_follow_content_width(page, rela, width, columns):
    page.set_viewport_size({'width': width, 'height': 1000})
    expect(page.locator('.person-card')).to_have_count(20)
    grid = page.locator('.people-grid')
    assert grid.evaluate('el => getComputedStyle(el).gridTemplateColumns.split(" ").length') == columns
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    card = page.locator('.person-card').first.bounding_box()
    assert card['height'] <= 210
    assert card['width'] <= 280
    page.screenshot(path=f'test-results/relationship-cards-{width}.png')


@pytest.mark.parametrize('theme,width', [('light', 1440), ('dark', 390)])
def test_long_identity_detail_and_circle_layout(page, rela, theme, width):
    person = rela['people'][0]
    person['display_name'] = 'AlexanderLongUnbrokenName' * 5 + '长姓名' * 15
    person['aliases'] = [{'alias_id': f'a_long_{i}', 'namespace': 'name', 'value': '长别名' * 20 + str(i)} for i in range(10)]
    rela['circles'][0]['name'] = '研究与产品共同讨论小组' * 8
    page.emulate_media(color_scheme=theme)
    page.set_viewport_size({'width': width, 'height': 1000})
    page.reload()
    first = page.locator('.person-card').first
    expect(first).to_contain_text(person['display_name'])
    assert page.locator('.management-body:visible').evaluate('el => el.scrollWidth <= el.clientWidth')
    if width > 768:
        page.get_by_role('button', name='收起会话列表', exact=True).click()
        expect(page.locator('.person-card')).to_have_count(20)
        assert page.locator('.person-card').first.bounding_box()['width'] <= 280
        assert 'cursor=' not in page.url
    first.focus()
    first.press('Enter')
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_role('heading', name=person['display_name'], exact=True)).to_be_visible()
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth')
    page.screenshot(path=f'test-results/relationship-detail-{theme}-{width}.png')
    tab = dialog.get_by_role('tab', name='稳定档案', exact=True)
    tab.focus()
    tab.press('End')
    expect(dialog.get_by_role('tab', name='别名与身份核对', exact=True)).to_be_focused()
    dialog.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(first).to_be_focused()
    page.get_by_role('navigation', name='人物分类').get_by_role('link', name='圈子', exact=True).click()
    circle = page.locator('.rela-circle-row').first
    expect(circle).to_be_visible()
    assert page.locator('.management-body:visible').evaluate('el => el.scrollWidth <= el.clientWidth')
    circle.click()
    circle_dialog = page.get_by_role('dialog', name='圈子详情', exact=True)
    expect(circle_dialog.get_by_role('heading', name=rela['circles'][0]['name'], exact=True)).to_be_visible()
    page.screenshot(path=f'test-results/relationship-circle-{theme}-{width}.png')
    circle_dialog.press('Escape')
    expect(circle).to_be_focused()


def test_doubled_text_remains_readable(page, rela):
    page.set_viewport_size({'width': 390, 'height': 1000})
    expect(page.locator('.person-card')).to_have_count(20)
    double_text = "els => { const sizes = els.map(el => parseFloat(getComputedStyle(el).fontSize)); els.forEach((el, i) => el.style.fontSize = sizes[i] * 2 + 'px'); }"
    page.locator('.relationship-page *').evaluate_all(double_text)
    assert page.locator('.management-body:visible').evaluate('el => el.scrollWidth <= el.clientWidth')
    page.locator('.person-card').first.click()
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_role('button', name='关闭人物档案', exact=True)).to_be_visible()
    dialog.locator('*').evaluate_all(double_text)
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth')
