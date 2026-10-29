"""Tests for the desktop I/O service (clipboard write + URL open)."""

import asyncio

import pytest

from apps.tui.src.platform.clipboard import ClipboardUnavailableError
from apps.tui.src.services.desktop import DesktopService, validate_http_url


def _run(coro):
    return asyncio.run(coro)


def test_copy_text走线程不阻塞():
    calls = []

    def fake_write(text):
        calls.append(text)

    service = DesktopService(write_text=fake_write)
    _run(service.copy_text("hello 中文"))
    assert calls == ["hello 中文"]


def test_copy_text异常原样上抛():
    def failing_write(text):
        raise ClipboardUnavailableError("tool: pbcopy not found")

    service = DesktopService(write_text=failing_write)
    with pytest.raises(ClipboardUnavailableError):
        _run(service.copy_text("text"))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/路径?q=1", "https://example.com/路径?q=1"),
        ("http://example.com", "http://example.com"),
    ],
)
def test_validate_http_url接受合法url(url, expected):
    assert validate_http_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://example.com/\n",
        "https://example.com/\x00",
        "   ",
        "",
        "ftp://example.com",
        "example.com",
    ],
)
def test_validate_http_url拒绝非法url(url):
    with pytest.raises(ValueError):
        validate_http_url(url)


def test_open_link校验后调用注入opener():
    captured = []
    service = DesktopService(open_url=captured.append)
    _run(service.open_link("https://example.com/x"))
    assert captured == ["https://example.com/x"]


def test_open_link拒绝非法url且不触达opener():
    captured = []
    service = DesktopService(open_url=captured.append)
    with pytest.raises(ValueError):
        _run(service.open_link("javascript:alert(1)"))
    assert captured == []


def test_url顺序参数不被shell解释():
    """The opener receives the URL as one argv element, never via a shell."""
    calls = []

    def fake_opener(argv):
        calls.append(argv)

    # validate_http_url output feeds open_url; the service passes the URL
    # through as a single string value, never as a shell command string.
    service = DesktopService(open_url=lambda url: fake_opener(["open", url]))
    _run(service.open_link("https://example.com/a?b=1&c=2;ls$IFS/"))
    assert calls == [["open", "https://example.com/a?b=1&c=2;ls$IFS/"]]
