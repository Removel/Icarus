import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("WEBUI_BROWSER_CHANNEL") or None,
        )
        yield browser
        browser.close()


@pytest.fixture
def page(browser, request):
    context = browser.new_context(viewport={"width": 1440, "height": 1080})
    page = context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(os.environ.get("WEBUI_BASE_URL", "http://127.0.0.1:5173"))
    page.get_by_role("heading", name="我的记忆", exact=True).wait_for()
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
