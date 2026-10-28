"""Keep existing surfaces still while read requests are pending."""
import re

from playwright.sync_api import expect
from test.rela_service import fake_rela


def test_button_keeps_its_size_when_hovered_pressed_and_focused(page):
    fake_rela(page)
    page.goto(page.url.split('#')[0] + '#/people')
    button = page.get_by_role('button', name='添加人物', exact=True)
    expect(button).to_be_visible()
    page.mouse.move(0, 0)
    before = button.bounding_box()
    button.hover()
    hovered = button.bounding_box()
    page.mouse.down()
    pressed = button.bounding_box()
    page.mouse.move(0, 0)
    page.mouse.up()
    button.focus()
    focused = button.bounding_box()
    for bounds in (hovered, pressed, focused):
        assert all(abs(before[key] - bounds[key]) <= 1 for key in ('x', 'y', 'width', 'height'))


def test_person_dialog_keeps_its_frame_while_loading(page):
    state = fake_rela(page)
    page.goto(page.url.split('#')[0] + '#/people')
    expect(page.locator('.person-card')).to_have_count(20)
    pending = []
    # A deep link outside the visible list has no card data to show provisionally.
    page.route(re.compile(r'/api/rela/v1/persons/p_000023_alpha(?:\?.*)?$'), lambda route: pending.append(route))
    page.goto(page.url.split('#')[0] + '#/people?person=p_000023_alpha&section=profile')
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_role('status', name='正在加载人物档案')).to_be_visible()
    before = dialog.bounding_box()
    for route in pending:
        route.fulfill(json={'person': state['people'][-1]})
    expect(dialog.get_by_text('open.unknown', exact=True)).to_be_visible()
    after = dialog.bounding_box()
    assert all(abs(before[key] - after[key]) <= 1 for key in ('x', 'y', 'width', 'height'))


def test_people_refresh_does_not_push_cards_down(page):
    fake_rela(page)
    page.goto(page.url.split('#')[0] + '#/people')
    card = page.locator('.person-card').first
    expect(page.locator('.person-card')).to_have_count(20)
    before = card.bounding_box()
    pending = []
    page.route(re.compile(r'/api/rela/v1/persons\?.*'), lambda route: pending.append(route))
    page.get_by_role('button', name='刷新人物', exact=True).click()
    expect(page.get_by_role('button', name='刷新人物', exact=True)).to_be_disabled()
    during = card.bounding_box()
    pending[0].fallback()
    expect(page.get_by_role('button', name='刷新人物', exact=True)).to_be_enabled()
    assert abs(before['y'] - during['y']) <= 1


def test_memory_detail_does_not_move_when_read_finishes(page):
    pending = []
    page.route(re.compile(r'/api/mem0/memories/mem_a8f2c1(?:/history)?(?:\?.*)?$'), lambda route: pending.append(route))
    page.locator('.memory-entry-open').first.click()
    dialog = page.locator('.memory-detail-modal .semi-modal')
    expect(dialog).to_be_visible()
    expect(page.get_by_role('button', name='修正内容', exact=True)).to_be_disabled()
    before = dialog.bounding_box()
    for route in pending:
        route.fallback()
    expect(page.get_by_role('button', name='修正内容', exact=True)).to_be_enabled()
    after = dialog.bounding_box()
    assert all(abs(before[key] - after[key]) <= 1 for key in ('x', 'y', 'width', 'height'))


def test_person_card_supplies_header_before_detail_request_finishes(page):
    page.emulate_media(reduced_motion='no-preference')
    state = fake_rela(page)
    page.goto(page.url.split('#')[0] + '#/people')
    expect(page.locator('.person-card')).to_have_count(20)
    pending = []
    page.route(re.compile(r'/api/rela/v1/persons/p_000001_alpha(?:\?.*)?$'), lambda route: pending.append(route))
    page.evaluate('''() => {
        window.firstPersonFrame = null;
        function record() {
            const dialog = document.querySelector('.rela-detail-modal .semi-modal');
            if (!dialog) { requestAnimationFrame(record); return; }
            window.firstPersonFrame = {
                name: dialog.querySelector('.rela-person-heading h2')?.textContent,
                visibleLoaders: [...dialog.querySelectorAll('.loading-indicator')].filter(
                    el => getComputedStyle(el).visibility !== 'hidden'
                ).length,
            };
        }
        requestAnimationFrame(record);
    }''')
    page.locator('.person-card').first.click()
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    # The clicked card already contains the identity. Do not flash an empty frame first.
    expect(dialog.get_by_role('heading', name='王明', exact=True)).to_be_visible()
    page.wait_for_function('window.firstPersonFrame !== null')
    assert page.evaluate('window.firstPersonFrame') == {'name': '王明', 'visibleLoaders': 0}
    expect(dialog.get_by_role('button', name='编辑基础信息', exact=True)).to_be_disabled()
    expect(dialog.get_by_role('button', name='添加档案', exact=True)).to_be_disabled()
    before = dialog.locator('.rela-person-heading').bounding_box()
    for route in pending:
        route.fulfill(json={'person': state['people'][0]})
    expect(dialog.get_by_role('button', name='编辑基础信息', exact=True)).to_be_enabled()
    after = dialog.locator('.rela-person-heading').bounding_box()
    assert all(abs(before[key] - after[key]) <= 1 for key in ('x', 'y', 'width', 'height'))


def test_failed_person_validation_keeps_card_identity_read_only(page):
    state = fake_rela(page)
    page.goto(page.url.split('#')[0] + '#/people')
    expect(page.locator('.person-card')).to_have_count(20)
    state['fail']['/v1/persons/p_000001_alpha'] = 503
    page.locator('.person-card').first.click()
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_role('alert')).to_be_visible()
    expect(dialog.get_by_role('heading', name='王明', exact=True)).to_be_visible()
    expect(dialog.get_by_role('button', name='编辑基础信息', exact=True)).to_be_disabled()
    expect(dialog.get_by_role('button', name='添加档案', exact=True)).to_be_disabled()
    assert all(method == 'GET' for method, _, _, _ in state['calls'])


def test_person_header_does_not_shift_when_profile_adds_scrollbar(page):
    page.emulate_media(reduced_motion='no-preference')
    fake_rela(page)
    page.goto(page.url.split('#')[0] + '#/people')
    expect(page.locator('.person-card')).to_have_count(20)
    pending = []
    page.route(re.compile(r'/api/rela/v1/persons/p_000001_alpha/profile\?.*'), lambda route: pending.append(route))
    page.locator('.person-card').first.click()
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_role('textbox', name='搜索档案 key 或内容', exact=True)).to_be_visible()
    before = dialog.locator('.rela-person-heading').bounding_box()
    for route in pending:
        route.fallback()
    expect(dialog.get_by_text('open.unknown', exact=True)).to_be_visible()
    after = dialog.locator('.rela-person-heading').bounding_box()
    assert all(abs(before[key] - after[key]) <= 1 for key in ('x', 'y', 'width', 'height'))


def test_circle_card_supplies_header_before_resolution_finishes(page):
    page.emulate_media(reduced_motion='no-preference')
    fake_rela(page)
    page.goto(page.url.split('#')[0] + '#/people/circles')
    expect(page.locator('.rela-circle-row')).to_have_count(1)
    pending = []
    page.route(re.compile(r'/api/rela/v1/circles\?.*limit=200.*'), lambda route: pending.append(route))
    page.locator('.rela-circle-row').first.click()
    dialog = page.get_by_role('dialog', name='圈子详情', exact=True)
    expect(dialog.get_by_role('heading', name='产品组', exact=True)).to_be_visible()
    before = dialog.locator('.rela-person-heading').bounding_box()
    for route in pending:
        route.fallback()
    expect(dialog.get_by_role('button', name='编辑圈子', exact=True)).to_be_enabled()
    after = dialog.locator('.rela-person-heading').bounding_box()
    assert all(abs(before[key] - after[key]) <= 1 for key in ('x', 'y', 'width', 'height'))
