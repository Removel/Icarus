"""Command-line entry point for the Icarus Textual terminal client."""

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

from apps.tui.src.app import IcarusTextualApp


def _load_runtime_dependencies() -> type[Any]:
    """Load Agent-only modules outside the Textual event-loop thread."""

    from apps.agent.src.application import AgentRuntimeService

    # Preload concrete projectors here as well. The registry itself is created
    # later on the Textual loop, after these imports can no longer block it.
    from apps.tui.src.event_pipeline.projectors import (
        AgentProjector,
        UserInputProjector,
    )

    del AgentProjector, UserInputProjector
    return AgentRuntimeService


async def _create_runtime_service(
    workspace_path: Path,
    session_id: str | None,
):
    service_type = await asyncio.to_thread(_load_runtime_dependencies)
    return service_type(
        workspace_path=workspace_path,
        session_id=session_id,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Icarus Agent terminal client")
    parser.add_argument(
        "--session-id",
        help="Optional trace session identifier",
    )
    return parser.parse_args(argv)


def run_app(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace_path = Path.cwd().resolve()

    async def runtime_factory():
        return await _create_runtime_service(
            workspace_path,
            args.session_id,
        )

    app = IcarusTextualApp(
        runtime_factory=runtime_factory,
        workspace_path=workspace_path,
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
