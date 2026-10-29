"""Command-line entry point for the Icarus Textual terminal client."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import build_config
from packages.runtime_environment import get_icarus_data_dir


def _load_gateway_client() -> type[Any]:
    from apps.tui.src.client.gateway import GatewayClient

    return GatewayClient


async def _create_gateway_client(
    workspace_path: Path,
    session_id: str | None,
    gateway_url: str,
    create_if_missing: bool = True,
):
    client_type = await asyncio.to_thread(_load_gateway_client)
    return client_type(
        url=gateway_url,
        workspace_path=workspace_path,
        session_id=session_id,
        create_if_missing=create_if_missing,
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
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Run offline demonstration data in the real TUI; no Gateway or model calls",
    )
    return parser.parse_args(argv)


def run_app(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = build_config(
        cwd=Path.cwd(),
        gateway_url=args.gateway_url,
        session_id=args.session_id,
        data_dir=get_icarus_data_dir(),
    )

    preview_client = None
    if args.preview:
        from dataclasses import replace

        from apps.tui.src.tools.preview import preview_scenario
        from apps.tui.src.tools.replay import ReplayRuntimeService

        config = replace(config, session_id="preview-session")
        preview_client = ReplayRuntimeService(
            preview_scenario(), events_per_second=8, session_id="preview-session"
        )

    async def runtime_factory(session_id: str | None, create_if_missing: bool):
        if preview_client is not None:
            return preview_client
        return await _create_gateway_client(
            config.workspace_path,
            session_id,
            config.gateway_url,
            create_if_missing,
        )

    from apps.tui.src.services.attachments import AttachmentsService

    app = IcarusTextualApp(
        config,
        client_factory=runtime_factory,
        attachments=AttachmentsService(config.resource_root),
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
