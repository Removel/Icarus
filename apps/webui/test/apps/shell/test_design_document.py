from pathlib import Path

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize('width', [390, 768, 1440])
def test_standalone_design_document_is_offline_and_interactive(browser, width):
    document = Path(__file__).resolve().parents[3] / 'docs' / 'design-system.html'
    context = browser.new_context(viewport={'width': width, 'height': 900})
    page = context.new_page()
    requests = []
    errors = []
    page.on('request', lambda request: requests.append(request.url))
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(document.as_uri())
        expect(page.get_by_role('heading', name='设计规范', exact=True)).to_be_visible()
        assert page.locator('.design-section').count() == 8
        expect(page.locator('#demo-toast')).not_to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('navigation', name='设计规范目录').get_by_role('link', name='弹窗与阅读').click()
        expect(page.get_by_role('heading', name='弹窗与阅读', exact=True)).to_be_focused()
        page.get_by_role('button', name='预览阅读弹窗', exact=True).click()
        dialog = page.get_by_role('dialog', name='阅读弹窗示例', exact=True)
        expect(dialog).to_be_visible()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role('button', name='预览阅读弹窗', exact=True)).to_be_focused()
        switch = page.get_by_role('switch', name='规范示例开关')
        switch.uncheck()
        expect(page.locator('#switch-label')).to_have_text('已停用')
        page.get_by_role('button', name='主要操作', exact=True).click()
        expect(page.get_by_role('status')).to_have_text('示例操作已完成')
        assert requests == [document.as_uri()]
        assert not errors
    finally:
        context.close()
