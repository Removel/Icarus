"""Icarus simple asynchronous REPL."""

import argparse
import asyncio
import sys
from typing import Callable, TextIO

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
)
from apps.agent.src.agent_orchestration.plugins import InputFinishedEvent
from apps.agent.src.application import AgentRuntimeService
from apps.agent.src.model_provider.types import Message, TextPart
from apps.tui.renderer import ReplRenderer


InputReader = Callable[[str], str]


async def run_repl(
    service: AgentRuntimeService,
    *,
    input_reader: InputReader = input,
    output: TextIO = sys.stdout,
) -> int:
    renderer = ReplRenderer(output)
    history: list[Message] = []
    await service.start()
    try:
        while True:
            try:
                prompt = await asyncio.to_thread(input_reader, "Icarus> ")
            except EOFError:
                break
            except KeyboardInterrupt:
                output.write("\n")
                break

            prompt = prompt.strip()
            if not prompt:
                continue
            if prompt.lower() in {"exit", "quit"}:
                break

            accepted = await service.submit(
                prompt=prompt,
                history_messages=list(history),
            )
            final_message: Message | None = None
            task_failed = False
            while True:
                _, event = await service.next_event()
                if event.correlation_id != accepted.task_id:
                    continue
                renderer.render(event)
                if isinstance(event, AgentCompletedEvent):
                    final_message = event.response.message
                elif isinstance(event, AgentErrorEvent):
                    task_failed = True
                elif isinstance(event, InputFinishedEvent):
                    task_failed = task_failed or event.status == "failed"
                    break

            renderer.finish_turn()
            if not task_failed and final_message is not None:
                history.extend(
                    [
                        Message("user", [TextPart(prompt)]),
                        final_message,
                    ]
                )
    finally:
        await service.stop()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Icarus Agent REPL")
    parser.add_argument(
        "--session-id",
        help="Optional trace session identifier",
    )
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    service = AgentRuntimeService(
        workspace_path=".",
        session_id=args.session_id,
    )
    return await run_repl(service)


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"Icarus failed to start: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
