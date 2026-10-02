import math
import os

import pytest
from playwright.sync_api import expect

from test_knowledge import fake_openkb, open_knowledge


def open_network(page, **kwargs):
    calls = fake_openkb(page, **kwargs)
    open_knowledge(page)
    page.get_by_role('link', name='文档关联', exact=True).click()
    expect(page.locator('.react-flow__node').first).to_be_visible()
    return calls


@pytest.mark.parametrize('width', [1440, 390])
def test_network_keeps_all_nodes_and_supports_wheel_pan_and_zoom_labels(page, width):
    page.set_viewport_size({'width': width, 'height': 1080})
    open_network(page)
    expect(page.locator('.graph-zoom')).to_have_text('87%')
    expect(page.locator('.graph-caption')).to_contain_text('5 个节点 · 4 条关系')
    nodes = page.locator('.react-flow__node')
    expect(nodes).to_have_count(5)
    points = nodes.evaluate_all('els => els.map(el => { const m = new DOMMatrix(el.style.transform); return [m.m41, m.m42]; })')
    # The three concepts no longer occupy one fixed column, and no nodes overlap.
    assert len({round(x) for x, _ in points}) == 5
    assert all(math.dist(a, b) > 60 for i, a in enumerate(points) for b in points[i + 1:])
    expect(page.locator('.document-node strong').filter(has_text='独立页面')).to_have_count(1)
    viewport = page.locator('.react-flow__viewport')
    before = viewport.get_attribute('style')
    box = page.locator('.document-canvas').bounding_box()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    page.mouse.wheel(0, 1600)
    expect(page.locator('.document-canvas')).to_have_attribute('data-labels', 'focused')
    expect(viewport).not_to_have_attribute('style', before)
    expect(nodes).to_have_count(5)
    before = viewport.get_attribute('style')
    page.mouse.move(box['x'] + 120, box['y'] + 120)
    page.mouse.down()
    page.mouse.move(box['x'] + 165, box['y'] + 165, steps=6)
    page.mouse.up()
    expect(viewport).not_to_have_attribute('style', before)
    page.get_by_role('button', name='适应画布', exact=True).click()
    expect(page.locator('.document-canvas')).to_have_attribute('data-labels', 'all')
    page.get_by_role('button', name='重置布局', exact=True).click()
    expect(page.locator('.graph-zoom')).to_have_text('87%')
    assert page.locator('.shell-main').evaluate('el => el.scrollWidth <= el.clientWidth')


def test_graph_fields_and_source_identifiers_remain_available(page):
    graph = {
        'nodes': [
            {'id': 'concepts/agent', 'label': 'Agent 约束', 'type': 'Concept',
             'description': '执行只使用传入的上下文。', 'sources': ['sources/paper.json'], 'in': 1, 'out': 0},
            {'id': 'concepts/runtime', 'label': '插件运行时', 'type': 'Infrastructure',
             'description': '路由与生命周期。', 'sources': [], 'in': 0, 'out': 1},
        ],
        'edges': [{'source': 'concepts/runtime', 'target': 'concepts/agent'}],
        'types': ['Concept', 'Infrastructure'],
    }
    calls = open_network(page, graph_data=graph)
    node = page.locator('.react-flow__node[aria-label="选择节点：Agent 约束"]')
    positions = page.locator('.react-flow__node').evaluate_all('els => els.map(el => el.style.transform)')
    viewport = page.locator('.react-flow__viewport').get_attribute('style')
    for label in ['Agent 约束', '插件运行时', 'Agent 约束']:
        page.locator(f'.react-flow__node[aria-label="选择节点：{label}"]').hover()
        expect(page.locator('.react-flow__node.is-active')).to_have_attribute('aria-label', '选择节点：' + label)
        expect(page.get_by_role('complementary', name='节点信息')).to_have_count(0)
        expect(page.get_by_role('dialog')).to_have_count(0)
    assert page.locator('.react-flow__node').evaluate_all('els => els.map(el => el.style.transform)') == positions
    expect(page.locator('.react-flow__viewport')).to_have_attribute('style', viewport)
    assert not any(path in ['page', 'document/source'] for path, _ in calls)
    node.click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    dialog = page.get_by_role('dialog', name='文档关联', exact=True)
    expect(dialog.locator('.graph-metadata')).to_contain_text('执行只使用传入的上下文。')
    expect(dialog.locator('.graph-metadata')).to_contain_text('0 / 1')
    expect(dialog.locator('.graph-metadata')).to_contain_text('Concept')
    dialog.get_by_text('更多字段', exact=True).click()
    expect(dialog.locator('.graph-metadata')).to_contain_text('concepts/agent')
    expect(dialog.locator('.graph-metadata')).to_contain_text('sources/paper.json')
    dialog.get_by_role('region', name='引用此内容的页面').get_by_role('button').click()
    page.get_by_role('button', name='查看关联', exact=True).click()
    expect(dialog.locator('.graph-metadata')).to_contain_text('Infrastructure')
    dialog.get_by_role('button', name='close', exact=True).click()
    source = page.locator('.react-flow__node[aria-label="选择节点：paper.pdf"]')
    source.focus()
    source.press('Enter')
    page.get_by_role('button', name='查看关联', exact=True).click()
    expect(dialog.locator('.graph-metadata')).to_contain_text('PDF · 3 页')
    details = dialog.locator('.graph-metadata details')
    if details.get_attribute('open') is None:
        details.locator('summary').click()
    for value in ['hash-paper', 'wiki/sources/paper.json', '可阅读']:
        expect(details).to_contain_text(value)


def test_mobile_pinch_zooms_the_graph_without_zooming_the_page(page):
    page.set_viewport_size({'width': 390, 'height': 900})
    cdp = page.context.new_cdp_session(page)
    cdp.send('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 2})
    page.goto(os.environ.get('WEBUI_BASE_URL', 'http://127.0.0.1:5173') + '/#/knowledge/graph?preview=1')
    expect(page.locator('.react-flow__node')).to_have_count(12)
    expect(page.locator('.document-canvas')).to_have_attribute('data-labels', 'all')
    box = page.locator('.document-canvas').bounding_box()
    zoom = page.locator('.graph-zoom')
    before = zoom.inner_text()
    center = box['x'] + box['width'] / 2
    y = box['y'] + 110
    def points(delta):
        return [{'x': center - 40 - delta, 'y': y, 'id': 0}, {'x': center + 40 + delta, 'y': y, 'id': 1}]
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': points(0)})
    for delta in [10, 20, 30, 40, 50, 60]:
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': points(delta)})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    expect(zoom).not_to_have_text(before)
    assert page.evaluate('visualViewport.scale') == 1


def test_reciprocal_links_keep_direction_and_missing_sources_stay_distinct(page):
    graph = {
        'nodes': [
            {'id': 'concepts/agent', 'label': 'Agent 约束', 'type': 'Concept', 'description': '',
             'sources': ['concepts/runtime', 'raw/missing.pdf'], 'in': 1, 'out': 1},
            {'id': 'concepts/runtime', 'label': '插件运行时', 'type': 'Concept', 'description': '',
             'sources': [], 'in': 1, 'out': 1},
        ],
        'edges': [{'source': a, 'target': b} for a, b in [
            ('concepts/agent', 'concepts/runtime'), ('concepts/runtime', 'concepts/agent'),
            ('concepts/agent', 'concepts/runtime'),
        ]],
        'types': ['Concept'],
    }
    open_network(page, graph_data=graph)
    expect(page.locator('.react-flow__node')).to_have_count(6)
    expect(page.locator('.react-flow__edge')).to_have_count(4)
    missing = page.locator('.document-node.is-unavailable')
    expect(missing).to_have_count(1)
    expect(missing).to_contain_text('raw/missing.pdf')
    links = page.locator('.react-flow__edge[aria-label="正文引用"]')
    points = links.locator('.react-flow__edge-path').evaluate_all('els => els.map(el => { const p = el.getPointAtLength(el.getTotalLength() / 2); return [p.x, p.y]; })')
    assert math.dist(*points) > 10
    for index, origin, target in [(0, 'Agent 约束', '插件运行时'), (1, '插件运行时', 'Agent 约束')]:
        links.nth(index).focus()
        links.nth(index).press('Enter')
        dialog = page.get_by_role('dialog', name='文档关联', exact=True)
        expect(dialog.locator('.relation-title')).to_have_text(origin)
        expect(dialog.get_by_role('region', name='引用的页面').locator('.is-highlighted')).to_contain_text(target)
        dialog.get_by_role('button', name='close', exact=True).click()
