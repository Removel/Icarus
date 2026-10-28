"""Start the Icarus WebUI production server as a native repository process."""

from __future__ import annotations

import os
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "icarus"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from environment import read_env_file

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = REPO_ROOT / "apps" / "webui"
ENV_FILE = REPO_ROOT / ".env"
DIST_DIR = APP_DIR / "apps" / "shell" / "dist"

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
ENV_KEYS = (
    "ICARUS_WEBUI_HOST",
    "ICARUS_WEBUI_PORT",
    "ICARUS_WEBUI_ORIGIN",
    "ICARUS_WEBUI_USER",
    "ICARUS_WEBUI_PASSWORD",
    "ICARUS_MEM0_ENDPOINT",
    "ICARUS_MEM0_API_KEY",
    "ICARUS_OPENKB_ENDPOINT",
    "ICARUS_OPENKB_API_TOKEN",
    "ICARUS_GATEWAY_ENDPOINT",
)

USAGE = "Usage: icarus_start.py [--help]"


def build_environment() -> dict[str, str]:
    """Merge the WebUI variables from the repository .env without overriding the process."""

    values = read_env_file(ENV_FILE)
    environment = dict(os.environ)
    for key in ENV_KEYS:
        if key not in environment and values.get(key):
            environment[key] = values[key]
    environment["ICARUS_WEBUI_HOST"] = (
        environment.get("ICARUS_WEBUI_HOST") or "127.0.0.1"
    )
    environment["ICARUS_WEBUI_PORT"] = (
        environment.get("ICARUS_WEBUI_PORT") or "8080"
    )
    return environment


def validate(environment: dict[str, str]) -> None:
    host = environment["ICARUS_WEBUI_HOST"]
    if host not in LOOPBACK_HOSTS:
        user = environment.get("ICARUS_WEBUI_USER") or ""
        password = environment.get("ICARUS_WEBUI_PASSWORD") or ""
        if not user or not password:
            raise SystemExit(
                f"ICARUS_WEBUI_HOST={host} is not loopback; set "
                "ICARUS_WEBUI_USER and ICARUS_WEBUI_PASSWORD together."
            )
    if not DIST_DIR.is_dir():
        raise SystemExit(
            f"WebUI build output is missing at {DIST_DIR}; "
            "run 'icarus install webui' first."
        )


def main(argv: list[str]) -> int:
    if any(value in {"-h", "--help"} for value in argv):
        print(USAGE)
        return 0
    if argv:
        print(USAGE, file=sys.stderr)
        return 2

    environment = build_environment()
    validate(environment)

    os.chdir(APP_DIR)
    try:
        os.execvpe("node", ["node", "server/index.mjs"], environment)
    except FileNotFoundError:
        raise SystemExit(
            "WebUI requires Node.js; run 'icarus install webui'."
        ) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
