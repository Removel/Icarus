"""App owns closing the desktop service as well as chat resources."""

import asyncio

from apps.tui.test.support import make_app, wait_ready


def test_app_wires_platform_opener(monkeypatch):
    import apps.tui.src.app as app_module

    opened = []
    monkeypatch.setattr(app_module, "open_url", opened.append, raising=False)
    async def run():
        app = make_app()
        await app.desktop.open_link("https://example.com/路径?q=1")
        assert opened == ["https://example.com/路径?q=1"]
        await app.desktop.close()
    asyncio.run(run())


def test_app_shutdown_drains_desktop_owner(monkeypatch):
    async def run():
        app = make_app()
        closed = []
        async def close():
            closed.append(True)
        monkeypatch.setattr(app.desktop, "close", close)
        async with app.run_test() as pilot:
            await wait_ready(pilot)
            app.request_shutdown()
            await app._cleanup_task
        assert closed == [True]
    asyncio.run(run())


def test_unmount_without_explicit_shutdown_closes_desktop_owner(monkeypatch):
    async def run():
        app = make_app()
        closed = []
        async def close():
            closed.append(True)
        monkeypatch.setattr(app.desktop, "close", close)
        async with app.run_test() as pilot:
            await wait_ready(pilot)
        assert closed == [True]
    asyncio.run(run())
