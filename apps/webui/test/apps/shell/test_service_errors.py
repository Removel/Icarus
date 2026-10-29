import pytest
from playwright.sync_api import expect

from test.navigation import follow_link


@pytest.fixture
def page(memory_page):
    return memory_page


@pytest.mark.parametrize('service,path,link', [
    ('记忆服务', '**/api/mem0/memories/page?**', None),
    ('知识服务', '**/api/v1/kbs', '知识库'),
])
@pytest.mark.parametrize('status,explanation', [
    (429, '请求过于频繁'),
    (503, '请确认服务已启动'),
    (504, '响应超时'),
])
def test_service_errors_explain_recovery_without_rendering_server_details(page, service, path, link, status, explanation):
    page.route(path, lambda route: route.fulfill(status=status, body='private backend response'))
    if link:
        follow_link(page, link)
    else:
        page.reload()
    alert = page.get_by_role('alert').filter(has_text=service).first
    expect(alert).to_contain_text(explanation)
    expect(alert).to_contain_text(str(status))
    expect(alert).not_to_contain_text('private backend response')
