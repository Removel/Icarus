"""Command-line entry point for the Icarus Textual terminal client."""

import argparse
from pathlib import Path
import sys

from apps.agent.src.application import AgentRuntimeService
from apps.tui.src.app import IcarusTextualApp


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
    service = AgentRuntimeService(
        workspace_path=workspace_path,
        session_id=args.session_id,
    )
    app = IcarusTextualApp(
        service=service,
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
