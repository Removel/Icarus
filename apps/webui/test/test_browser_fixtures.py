"""Chat setup must not pay for a Memory bootstrap or leave an old RPC alive."""

from test.packages.chat_app.test_chat import gateway


def test_chat_opens_directly_and_replaces_an_existing_mock(app_page):
    page = app_page
    navigations, memory_requests = [], []
    page.on('request', lambda request: navigations.append(request.url)
            if request.is_navigation_request() and request.frame == page.main_frame else None)
    page.on('request', lambda request: memory_requests.append(request.url) if '/api/mem0/' in request.url else None)
    first, _ = gateway(page)
    second, _ = gateway(page)
    assert len([url for url in navigations if url.startswith('http')]) == 2
    assert not memory_requests
    assert len(first['sockets']) == len(second['sockets']) == 1
    assert sum(call['method'] == 'session.subscribe' for call in second['calls']) == 1
