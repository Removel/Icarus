"""Replay an Icarus TUI JSONL fixture without model API calls."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apps.tui.src.replay import ReplayRuntimeService, load_replay
from apps.tui.src.transcript import transcript_from_scenario


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay deterministic Icarus TUI events"
    )
    parser.add_argument("events", type=Path, help="JSONL replay fixture")
    parser.add_argument(
        "--tui-real",
        action="store_true",
        help="Replay in the complete Textual shell",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=8,
        help="Events per second for --tui-real (default: 8)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenario = load_replay(args.events)
    if not args.tui_real:
        sys.stdout.write(transcript_from_scenario(scenario))
        return 0

    # Delayed import keeps transcript replay independent from Textual app startup.
    from apps.tui.src.app import IcarusTextualApp

    service = ReplayRuntimeService(
        scenario, events_per_second=args.speed
    )
    app = IcarusTextualApp(
        service=service,
        workspace_path=Path.cwd().resolve(),
    )
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())
