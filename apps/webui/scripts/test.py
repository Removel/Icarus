"""Run offline WebUI tests with a dedicated dev server and deterministic cleanup."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


APP = Path(__file__).resolve().parents[1]


def main() -> int:
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm is required; install the app's pinned toolchain")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(os.environ)
    # Default regression tests must not inherit opt-ins to real services/models.
    for key in tuple(environment):
        if key.startswith("WEBUI_LIVE_"):
            del environment[key]
    environment.update({
        "WEBUI_BASE_URL": f"http://127.0.0.1:{port}",
        # Vite reads the root .env. Explicit process values take precedence, so
        # an unmocked offline request cannot reach a developer's real services.
        "ICARUS_MEM0_ENDPOINT": "http://127.0.0.1:9",
        "ICARUS_OPENKB_ENDPOINT": "http://127.0.0.1:9",
        "ICARUS_GATEWAY_ENDPOINT": "http://127.0.0.1:9",
        "ICARUS_RELATIONSHIP_ENDPOINT": "http://127.0.0.1:9",
        "ICARUS_MEM0_API_KEY": "offline-test-only",
        "ICARUS_OPENKB_API_TOKEN": "offline-test-only",
        "RELA_MEM_API_TOKEN": "offline-test-only",
    })
    results = APP / "test-results"
    results.mkdir(exist_ok=True)
    with (results / "dev-server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [pnpm, "--filter", "@icarus/shell", "dev", "--port", str(port), "--strictPort"],
            cwd=APP, env=environment, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 60
            while True:
                if server.poll() is not None:
                    raise RuntimeError("WebUI dev server exited; see test-results/dev-server.log")
                try:
                    with urlopen(environment["WEBUI_BASE_URL"], timeout=2) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("WebUI dev server readiness deadline exceeded")
                time.sleep(.25)
            return subprocess.call([pnpm, "test", *sys.argv[1:]], cwd=APP, env=environment)
        finally:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                try:
                    os.killpg(server.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(server.pid, signal.SIGKILL)
                else:
                    server.kill()
                server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
