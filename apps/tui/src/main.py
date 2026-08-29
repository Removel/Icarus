"""Command-line entry point for the Icarus Textual terminal client."""

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

from apps.tui.src.app import IcarusTextualApp
from packages.runtime_environment import get_icarus_data_dir


def _load_gateway_client() -> type[Any]:
    from apps.tui.src.gateway_client import GatewayClient

    return GatewayClient


async def _create_gateway_client(
    workspace_path: Path, session_id: str | None, gateway_url: str
):
    client_type = await asyncio.to_thread(_load_gateway_client)
    return client_type(
        url=gateway_url,
        workspace_path=workspace_path,
        session_id=session_id,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Icarus Agent terminal client")
    parser.add_argument(
        "--session-id",
        help="Optional Session identifier",
    )
    parser.add_argument(
        "--gateway-url",
        default="ws://127.0.0.1:8765/rpc",
        help="Agent Gateway WebSocket URL",
    )
    return parser.parse_args(argv)


def run_app(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace_path = Path.cwd().resolve()
    resource_root = get_icarus_data_dir() / "incoming"

    async def runtime_factory():
        return await _create_gateway_client(
            workspace_path,
            args.session_id,
            args.gateway_url,
        )

    app = IcarusTextualApp(
        runtime_factory=runtime_factory,
        workspace_path=workspace_path,
        resource_root=resource_root,
    )
    result = app.run()
    if isinstance(result, int):
        return result
    return app.return_code or 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run_app(argv)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(
            f"Icarus failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
