"""Focused tests for composer submission routing."""

from __future__ import annotations

from pathlib import Path

from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.domain.commands import create_default_command_registry
from apps.tui.src.services.submission_router import (
    Enqueue,
    Reject,
    RunCommand,
    SubmissionRouter,
    Unavailable,
)
from apps.tui.src.domain.submission import DraftImage, PendingMessage


def make_router() -> SubmissionRouter:
    return SubmissionRouter(create_default_command_registry())


def test_plain_message_enqueues() -> None:
    plan = make_router().route(
        PendingMessage("hello"),
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )

    assert isinstance(plan, Enqueue)
    assert plan.submission.text == "hello"


def test_image_message_enqueues_with_original_submission() -> None:
    image = DraftImage("image1", Path("/tmp/image.png"))
    submission = PendingMessage("look [#image1]", (image,))

    plan = make_router().route(
        submission,
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )

    assert isinstance(plan, Enqueue)
    assert plan.submission is submission
    assert plan.submission.images == (image,)


def test_exit_command_runs_local_handler() -> None:
    plan = make_router().route(
        PendingMessage("/exit"),
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )

    assert isinstance(plan, RunCommand)
    assert plan.definition.handler_name == "exit_app"


def test_unknown_command_is_rejected() -> None:
    plan = make_router().route(
        PendingMessage("/nope"),
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )

    assert isinstance(plan, Reject)
    assert plan.reason == "Unknown command: /nope"
    assert plan.title == "Unknown command"


def test_command_with_arguments_is_rejected() -> None:
    plan = make_router().route(
        PendingMessage("/exit now"),
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )

    assert isinstance(plan, Reject)
    assert plan.title == "Command not run"


def test_command_with_attachment_is_rejected() -> None:
    image = DraftImage("image1", Path("/tmp/image.png"))
    plan = make_router().route(
        PendingMessage("/clear [#image1]", (image,)),
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )

    assert isinstance(plan, Reject)
    assert plan.reason == "Remove image attachments before running this command."


def test_idle_command_while_busy_is_unavailable() -> None:
    plan = make_router().route(
        PendingMessage("/resume"),
        phase=RuntimePhase.RUNNING,
        can_run_session_command=False,
    )

    assert isinstance(plan, Unavailable)
    assert plan.reason == "Session commands are only available while idle."
    assert plan.title == "Session command not run"


def test_message_while_switching_is_unavailable() -> None:
    plan = make_router().route(
        PendingMessage("hello"),
        phase=RuntimePhase.SWITCHING,
        can_run_session_command=False,
    )

    assert isinstance(plan, Unavailable)
    assert plan.title == "Session command not run"
