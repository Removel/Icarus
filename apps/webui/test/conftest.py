import os
import json
from pathlib import Path

import pytest
from test.mem0_service import fake_mem0


@pytest.fixture(scope="session")
def browser():
    # Imported lazily so non-browser tests can run without the Playwright extra.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("WEBUI_BROWSER_CHANNEL") or None,
        )
        yield browser
        browser.close()


@pytest.fixture
def page(browser, request):
    context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(10000)
    page.set_default_navigation_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    def memory_context(socket):
        def receive(raw):
            request = json.loads(raw)
            if request['method'] == 'memory.get_context':
                workspace = request.get('params', {}).get('workspace_path')
                result = {'user_id': 'configured-user', 'agent_id': 'icarus', 'run_id': 'global'}
                if workspace:
                    result.update(run_id='workspace:1234567890abcdef', workspace_path=workspace)
                socket.send(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}))
        socket.on_message(receive)
    page.route_web_socket('**/rpc', memory_context)
    page.mem0_rows = fake_mem0(page)
    page.goto(os.environ.get("WEBUI_BASE_URL", "http://127.0.0.1:5173"), wait_until="domcontentloaded")
    page.get_by_role("heading", name="记忆", exact=True).wait_for()
    page.locator('.memory-entry').first.wait_for()
    yield page
    if getattr(request.node, "rep_call", None) and request.node.rep_call.failed:
        output = Path("test-results")
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f"{request.node.name}.png"), full_page=True)
        (output / f"{request.node.name}.html").write_text(page.content(), encoding="utf-8")
    context.close()
    assert not errors, errors


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)
