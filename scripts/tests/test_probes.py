from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

from registry import Component, Probe


@contextmanager
def http_server(*, body=b'{"status":"ready","extra":1}', status=200, headers=None, delay=0,
                chunk_interval=0, header_interval=0):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
            time.sleep(delay)
            try:
                if header_interval:
                    raw_headers = f"HTTP/1.0 {status} Ready\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                    for byte in raw_headers:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(header_interval)
                else:
                    self.send_response(status)
                    for key, value in (headers or {}).items():
                        self.send_header(key, value)
                    self.end_headers()
                if chunk_interval:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(chunk_interval)
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def http_component(endpoint, **kwargs):
    return Component("service", "app", "local", "process", endpoint=endpoint,
                     probe=Probe("http", path="/health", **kwargs))


def test_http_probe_matches_json_subset_over_real_http():
    with http_server() as (endpoint, received):
        from probes import probe

        result = probe(http_component(endpoint + "/", expect_body={"status": "ready"}), {})
        assert result is not None and result.ok, "a ready HTTP JSON subset must be reachable"
        assert result.state == "reachable"
        assert received[0][0] == "/health"


@pytest.fixture
def api():
    import probes

    return probes


@pytest.mark.parametrize("status,expected,ok", [(200, None, True), (302, None, True),
                                               (404, None, False), (503, None, False),
                                               (200, 201, False), (503, 503, True)])
def test_http_status_policy_including_http_error_responses(api, status, expected, ok):
    with http_server(status=status) as (endpoint, _):
        result = api.probe(http_component(endpoint, expect_status=expected), {})
    assert result.ok is ok
    assert result.state == ("reachable" if ok else "unreachable")


@pytest.mark.parametrize("body", [b'not-json', b'[]', b'null', b'"ready"', b'{"status":"starting"}', b'{}'])
def test_json_expectation_requires_object_subset(api, body):
    with http_server(body=body) as (endpoint, _):
        result = api.probe(http_component(endpoint, expect_body={"status": "ready"}), {})
    assert not result.ok
    assert result.state == "unreachable"
    assert body.decode() not in result.detail


def test_json_subset_supports_all_registry_scalar_types(api):
    expected = {"ready": True, "count": 3, "rate": 1.5, "empty": None}
    with http_server(body=json.dumps({**expected, "extra": "allowed"}).encode()) as (endpoint, _):
        assert api.probe(http_component(endpoint, expect_body=expected), {}).ok


@pytest.mark.parametrize("header,key,value", [(None, "authorization", "Bearer private-token"),
                                               ("X-API-Key", "x-api-key", "private-token")])
def test_token_is_bound_to_declared_request_header(api, header, key, value):
    with http_server() as (endpoint, received):
        result = api.probe(http_component(endpoint, token_env="HEALTH_TOKEN", header=header),
                           {"HEALTH_TOKEN": "private-token"})
    assert result.ok
    assert received[0][1][key] == value
    assert "private-token" not in result.detail


@pytest.mark.parametrize("environ", [{}, {"HEALTH_TOKEN": ""}])
def test_missing_token_fails_safely_without_contacting_endpoint(api, environ):
    with http_server() as (endpoint, received):
        result = api.probe(http_component(endpoint, token_env="HEALTH_TOKEN"), environ)
    assert not result.ok
    assert "HEALTH_TOKEN" in result.detail
    assert received == []


def test_redirect_never_forwards_custom_auth_or_contacts_destination(api):
    with http_server() as (target, target_received):
        with http_server(status=302, headers={"Location": target + "/stolen"}) as (endpoint, received):
            result = api.probe(http_component(endpoint, token_env="TOKEN", header="X-API-Key",
                                              expect_status=200), {"TOKEN": "do-not-forward"})
    assert not result.ok
    assert received[0][1]["x-api-key"] == "do-not-forward"
    assert target_received == []
    assert "do-not-forward" not in result.detail


@pytest.mark.parametrize("expect_body", [None, {"status": "ready"}])
def test_response_limit_is_enforced_even_without_json_expectation(api, expect_body):
    with http_server(body=b"x" * 33) as (endpoint, _):
        result = api.probe(http_component(endpoint, max_response_bytes=32, expect_body=expect_body), {})
    assert not result.ok
    assert "limit" in result.detail


def test_default_response_limit_is_bounded(api):
    with http_server(body=b"x" * 65537) as (endpoint, _):
        assert not api.probe(http_component(endpoint), {}).ok


def test_response_at_limit_is_accepted(api):
    with http_server(body=b"1234") as (endpoint, _):
        assert api.probe(http_component(endpoint, max_response_bytes=4), {}).ok


def test_configured_timeout_bounds_real_http_wait(api):
    with http_server(delay=0.3) as (endpoint, _):
        start = time.monotonic()
        result = api.probe(http_component(endpoint, timeout_seconds=0.05), {})
        elapsed = time.monotonic() - start
    assert not result.ok
    assert elapsed < 0.25


@pytest.mark.parametrize("trickle", ["body", "headers"])
def test_total_deadline_bounds_trickled_response_without_leaking_resources(api, trickle):
    import multiprocessing

    # Startup is part of the production budget. Leave enough room for a spawned
    # interpreter under suite/CI load, but stream for at least 1.5s so an ordinary
    # per-read socket timeout would still incorrectly finish after our deadline.
    timeout = 0.8
    options = {"chunk_interval": 0.04} if trickle == "body" else {"header_interval": 0.04}
    with http_server(body=b"1234567890" * 4, **options) as (endpoint, received):
        # Warm the probe before checking descriptor ownership to exclude lazy imports.
        assert api.probe(http_component(endpoint, timeout_seconds=0.01), {}).state == "unreachable"
        received.clear()
        existing_threads = set(threading.enumerate())
        existing_children = set(multiprocessing.active_children())
        descriptors = Path("/proc/self/fd")
        descriptor_count = len(tuple(descriptors.iterdir())) if descriptors.is_dir() else None
        start = time.monotonic()
        result = api.probe(http_component(endpoint, timeout_seconds=timeout), {})
        elapsed = time.monotonic() - start
        assert not result.ok
        assert elapsed < timeout + 0.15
        assert received, "deadline must interrupt an actual HTTP request, not only worker startup"
        assert set(multiprocessing.active_children()) == existing_children
        assert not [thread for thread in threading.enumerate()
                    if thread not in existing_threads and not thread.name.endswith("(process_request_thread)")]
        # Server-side handlers may still be unwinding; no parent client descriptors remain.
        if descriptor_count is not None:
            assert len(tuple(descriptors.iterdir())) <= descriptor_count + 1


def test_default_timeout_is_two_seconds(api):
    with http_server(delay=2.4) as (endpoint, _):
        start = time.monotonic()
        result = api.probe(http_component(endpoint), {})
        elapsed = time.monotonic() - start
    assert not result.ok
    assert 1.7 < elapsed < 2.3


def test_detail_does_not_echo_response_endpoint_or_credentials(api):
    secret = "response-secret-token"
    with http_server(body=secret.encode(), status=503) as (endpoint, _):
        result = api.probe(http_component(endpoint + "/sensitive-path?value=private",
                                          token_env="TOKEN"), {"TOKEN": secret})
    assert not result.ok
    assert all(value not in result.detail for value in (secret, "sensitive-path", "private", endpoint))


def test_invalid_header_token_is_sanitized(api):
    with http_server() as (endpoint, received):
        result = api.probe(http_component(endpoint, token_env="TOKEN"), {"TOKEN": "secret\r\nX-Other: leak"})
    assert not result.ok
    assert "secret" not in result.detail and "leak" not in result.detail
    assert received == []


@pytest.mark.parametrize("token_env", [None, "IRRELEVANT_TCP_TOKEN"])
def test_tcp_uses_probe_override_and_closes_connection(api, token_env):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(1)
        component = Component("db", "middleware", "local", "compose", host="invalid.test", port=1,
                              probe=Probe("tcp", host="127.0.0.1", port=server.getsockname()[1],
                                          token_env=token_env))
        result = api.probe(component, {})
        connection, _ = server.accept()
        with connection:
            connection.settimeout(1)
            assert connection.recv(1) == b""
    assert result.ok and result.state == "reachable"


# Only this controlled latency seam uses fork; production keeps spawn even when
# earlier test-owned HTTP handlers are still unwinding in daemon threads.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded.*:DeprecationWarning")
@pytest.mark.parametrize("delay_at", ["dns", "multiple_addresses"])
def test_tcp_total_deadline_covers_dns_and_all_addresses_without_leaks(api, monkeypatch, tmp_path, delay_at):
    import multiprocessing

    # Production always asks for spawn. This Linux-only test substitutes fork so
    # patched socket operations are actually inherited, not assumed to cross spawn.
    # Only resolver/connect latency is controlled; the final socket is real loopback.
    context = multiprocessing.get_context("fork")
    monkeypatch.setattr(api.multiprocessing, "get_context", lambda method: context)
    original_getaddrinfo, original_socket = socket.getaddrinfo, socket.socket
    entered = tmp_path / "entered.txt"
    timeout = 0.8
    with original_socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        addresses = original_getaddrinfo("127.0.0.1", port, 0, socket.SOCK_STREAM)

        def getaddrinfo(*args, **kwargs):
            if delay_at == "dns":
                entered.write_text("dns")
                time.sleep(1.8)
                return addresses
            return addresses * 4

        class SlowAddresses(original_socket):
            attempts = 0

            def connect(self, address):
                type(self).attempts += 1
                entered.write_text(str(type(self).attempts))
                if type(self).attempts <= 3:
                    # Every individual attempt is below the socket timeout; only
                    # a total-operation deadline can interrupt the address loop.
                    time.sleep(0.55)
                    raise socket.timeout("controlled connect timeout")
                return super().connect(address)

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        if delay_at == "multiple_addresses":
            monkeypatch.setattr(socket, "socket", SlowAddresses)
        component = Component("db", "middleware", "remote", "none", host="127.0.0.1",
                              port=port, probe=Probe("tcp", timeout_seconds=timeout))
        existing_threads = set(threading.enumerate())
        existing_children = set(multiprocessing.active_children())
        descriptors = Path("/proc/self/fd")
        descriptor_count = len(tuple(descriptors.iterdir()))
        start = time.monotonic()
        result = api.probe(component, {"UNRELATED_SECRET": "never-pass-to-tcp"})
        elapsed = time.monotonic() - start
        assert entered.exists(), "deadline must interrupt resolver/connect work, not just startup"
        assert not result.ok, f"{delay_at} incorrectly returned success after {elapsed:.3f}s"
        assert elapsed < timeout + 0.2
        assert "never-pass-to-tcp" not in result.detail
        assert set(multiprocessing.active_children()) == existing_children
        # Earlier HTTP handlers may finish while this probe runs; only new
        # threads indicate leakage from the client under test.
        assert not set(threading.enumerate()) - existing_threads
        assert len(tuple(descriptors.iterdir())) <= descriptor_count


def test_tcp_falls_back_to_component_address(api):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        component = Component("db", "middleware", "remote", "none", host="127.0.0.1",
                              port=server.getsockname()[1], probe=Probe("tcp"))
        assert api.probe(component, {}).ok


def test_refused_tcp_and_http_are_results_not_exceptions(api):
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        component = Component("db", "middleware", "local", "compose", host="127.0.0.1",
                              port=port, probe=Probe("tcp"))
        for target in (component, http_component(f"http://127.0.0.1:{port}")):
            result = api.probe(target, {})
            assert not result.ok and result.state == "unreachable"


def test_missing_probe_returns_safe_unreachable_result(api):
    component = Component("app", "app", "local", "process")
    result = api.probe(component, {})
    assert not result.ok and result.state == "unreachable"


def test_direct_dataclass_endpoint_credentials_are_never_sent(api):
    with http_server() as (endpoint, received):
        result = api.probe(http_component(endpoint.replace("http://", "http://user:secret@")), {})
    assert not result.ok
    assert "secret" not in result.detail
    assert received == []
