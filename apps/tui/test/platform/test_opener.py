"""Browser launch contracts: fixed host commands and untrusted URL data."""

from types import SimpleNamespace

import pytest

from apps.tui.src.platform import opener


@pytest.mark.parametrize("platform,wsl,command", [
    ("darwin", False, ["open"]),
    ("linux", False, ["xdg-open"]),
])
def test_native_url_is_one_argument(monkeypatch, platform, wsl, command):
    calls = []
    monkeypatch.setattr(opener.sys, "platform", platform)
    monkeypatch.setattr(opener, "is_wsl", lambda: wsl)
    monkeypatch.setattr(opener.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=0))
    url = "https://example.com/路径?q=a&next=1"
    opener.open_url(url)
    assert calls[0][0] == command + [url]
    assert calls[0][1].get("shell", False) is False
    assert calls[0][1]["timeout"] > 0


def test_windows_uses_system_association(monkeypatch):
    calls = []
    monkeypatch.setattr(opener.sys, "platform", "win32")
    monkeypatch.setattr(opener.os, "startfile", calls.append, raising=False)
    opener.open_url("https://example.com")
    assert calls == ["https://example.com"]


def test_wsl_uses_fixed_powershell_with_url_only_in_stdin(monkeypatch):
    calls = []
    monkeypatch.setattr(opener.sys, "platform", "linux")
    monkeypatch.setattr(opener, "is_wsl", lambda: True)
    monkeypatch.setattr(opener, "run_host", lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=0))
    url = "https://example.com/?x=%22&next=1;abc"
    opener.open_url(url)
    argv, kwargs = calls[0]
    assert "powershell.exe" in argv[0].lower()
    assert url not in " ".join(argv)
    assert kwargs["stdin"] == url.encode("utf-8")
    assert "Start-Process" in argv[-1]


@pytest.mark.parametrize("url", [
    "", "javascript:alert(1)", "file:///tmp/a", "http:///missing",
    "https://", "https://a\n", "https://a\x00", "https://a\x7f",
    " https://a", "https://a ", "https://exa mple.com",
    "https://a:bad", "https://a:99999", "https://[bad]/",
])
def test_invalid_url_never_reaches_os(monkeypatch, url):
    monkeypatch.setattr(opener.subprocess, "run", lambda *a, **kw: pytest.fail("invalid URL reached process"))
    with pytest.raises(ValueError):
        opener.open_url(url)


@pytest.mark.parametrize("url", [
    "HTTP://example.com/path", "https://example.com/路径?q=1", "http://[::1]:80/",
    "https://example.com/?next=https://other.test/path", "https://example.com/a//b",
])
def test_valid_structural_urls(url):
    assert opener.validate_http_url(url) == url


def test_launch_failure_is_oserror(monkeypatch):
    monkeypatch.setattr(opener.sys, "platform", "darwin")
    monkeypatch.setattr(opener.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(OSError):
        opener.open_url("https://example.com")
