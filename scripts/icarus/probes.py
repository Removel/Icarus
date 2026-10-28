"""Bounded, credential-safe HTTP/TCP reachability checks using only stdlib."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPException
import json
import multiprocessing
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from registry import Component


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    state: str
    detail: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Even a custom authentication header must never reach a redirect target.
        return None


def _result(ok: bool, detail: str) -> ProbeResult:
    return ProbeResult(ok, "reachable" if ok else "unreachable", detail)


def probe(component: Component, environ: Mapping[str, str]) -> ProbeResult:
    """Bound HTTP/TCP's entire operation, including DNS and address retries.

    A spawn worker avoids process-global signals and forking threaded callers.
    Its lifetime and pipe are always closed here, including timeout/error paths.
    """
    spec = component.probe
    if spec is None or spec.type not in {"http", "tcp"}:
        return _probe(component, {})
    if spec.type == "http" and spec.token_env and not environ.get(spec.token_env):
        return _result(False, f"missing probe secret {spec.token_env}")
    deadline = time.monotonic() + spec.timeout_seconds
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    # Pass only HTTP's declared probe credential, never unrelated or TCP secrets.
    credentials = ({spec.token_env: environ[spec.token_env]}
                   if spec.type == "http" and spec.token_env else {})
    worker = context.Process(target=_probe_worker, args=(component, credentials, sender), daemon=True)
    try:
        worker.start()
        sender.close()
        if receiver.poll(max(0, deadline - time.monotonic())):
            result = receiver.recv()
            if time.monotonic() <= deadline:
                return result
        return _result(False, "probe connection failed or timed out")
    except (EOFError, OSError, ValueError):
        return _result(False, "probe connection failed or timed out")
    finally:
        receiver.close()
        sender.close()
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=0.05)
            if worker.is_alive():
                worker.kill()
                worker.join()
        worker.close()


def _probe_worker(component: Component, environ: Mapping[str, str], sender) -> None:
    try:
        try:
            result = _probe(component, environ)
        except Exception:
            # Worker failures must not print credential-bearing tracebacks.
            result = _result(False, "probe connection failed or timed out")
        try:
            sender.send(result)
        except (EOFError, OSError):
            pass  # Parent may have expired and closed its pipe.
    finally:
        sender.close()


def _probe(component: Component, environ: Mapping[str, str]) -> ProbeResult:
    """Inspect one endpoint; never echo URLs, response bodies or exception values.

    Redirect responses are checked as-is, without a second request. Declared
    tokens are resolved only from environ; a missing token fails before I/O.
    """
    spec = component.probe
    if spec is None:
        return _result(False, "no probe configured")
    try:
        if spec.type == "tcp":
            host, port = spec.host or component.host, spec.port or component.port
            if not host or not port:
                return _result(False, "TCP probe requires host and port")
            with socket.create_connection((host, port), timeout=spec.timeout_seconds):
                return _result(True, "TCP connection established")
        if spec.type != "http":
            return _result(False, "unsupported probe type")
        endpoint = urlsplit(component.endpoint or "")
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username is not None or endpoint.password is not None):
            return _result(False, "HTTP probe requires an HTTP(S) endpoint without credentials")
        headers = {}
        if spec.token_env:
            token = environ.get(spec.token_env)
            if not token:
                return _result(False, f"missing probe secret {spec.token_env}")
            headers[spec.header or "Authorization"] = token if spec.header else f"Bearer {token}"
        request = Request(component.endpoint.rstrip("/") + spec.path, headers=headers)
        # Direct requests avoid leaking probe credentials through inherited proxies.
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=spec.timeout_seconds)
        except HTTPError as error:
            response = error
        with response:
            status = response.code
            status_ok = (status == spec.expect_status if spec.expect_status is not None
                         else 200 <= status < 400)
            if not status_ok:
                return _result(False, f"unexpected HTTP status {status}")
            body = response.read(spec.max_response_bytes + 1)
        if len(body) > spec.max_response_bytes:
            return _result(False, "HTTP response exceeds size limit")
        if spec.expect_body is not None:
            try:
                actual = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                return _result(False, "HTTP response is not a JSON object")
            if not isinstance(actual, dict) or any(
                key not in actual or actual[key] != value for key, value in spec.expect_body.items()
            ):
                return _result(False, "HTTP JSON expectation not met")
        return _result(True, f"HTTP status {status}")
    except (URLError, OSError, ValueError, UnicodeError, HTTPException):
        return _result(False, "probe connection failed or timed out")
