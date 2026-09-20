"""Validation and compatibility projection for replayable Agent messages."""

from collections.abc import Sequence
from copy import deepcopy

from apps.agent.src.model_provider.types import Message, TextPart


INTERRUPTED_MESSAGE = "Operation interrupted."


class MessageHistoryError(ValueError):
    """A message sequence cannot be replayed without changing its meaning."""


def validate_run_messages(
    messages: Sequence[Message],
    *,
    require_final_assistant: bool,
) -> tuple[Message, ...]:
    """Validate one Run delta and return an immutable shallow snapshot."""

    snapshot = tuple(deepcopy(list(messages)))
    if not snapshot:
        raise MessageHistoryError("run history cannot be empty")
    if snapshot[0].role != "user":
        raise MessageHistoryError("run history must start with a user message")

    seen_tool_call_ids: set[str] = set()
    index = 0
    previous_role: str | None = None
    while index < len(snapshot):
        message = snapshot[index]
        _validate_message_shape(message)
        if message.role == "system":
            raise MessageHistoryError("run history cannot contain a system message")
        if message.role == "tool":
            raise MessageHistoryError("tool result has no preceding tool call group")
        if message.role == "user":
            if previous_role == "user":
                raise MessageHistoryError("run history contains consecutive user messages")
            previous_role = "user"
            index += 1
            continue

        if not message.tool_calls:
            if not _has_visible_content(message):
                raise MessageHistoryError("assistant message cannot be empty")
            if previous_role == "assistant":
                raise MessageHistoryError(
                    "run history contains consecutive assistant messages"
                )
            previous_role = "assistant"
            index += 1
            continue

        if previous_role == "assistant":
            raise MessageHistoryError(
                "assistant tool call cannot follow an assistant message"
            )
        call_ids = [call.id for call in message.tool_calls]
        if any(not call_id for call_id in call_ids):
            raise MessageHistoryError("tool call id cannot be empty")
        if len(set(call_ids)) != len(call_ids):
            raise MessageHistoryError("tool call ids must be unique within a group")
        if seen_tool_call_ids.intersection(call_ids):
            raise MessageHistoryError("tool call ids must be unique within a run")
        seen_tool_call_ids.update(call_ids)

        results = snapshot[index + 1 : index + 1 + len(call_ids)]
        if len(results) != len(call_ids):
            raise MessageHistoryError("tool call group is missing results")
        result_ids = []
        for result in results:
            _validate_message_shape(result)
            if result.role != "tool":
                raise MessageHistoryError("tool call group is interrupted before completion")
            result_ids.append(result.tool_call_id)
        if result_ids != call_ids:
            raise MessageHistoryError(
                "tool results must match tool calls in their original order"
            )
        previous_role = "tool"
        index += len(call_ids) + 1

    if require_final_assistant:
        final = snapshot[-1]
        if (
            final.role != "assistant"
            or final.tool_calls
            or not _has_visible_content(final)
        ):
            raise MessageHistoryError(
                "completed history must end with a visible assistant message"
            )
    return snapshot


def close_interrupted_run(messages: Sequence[Message]) -> tuple[Message, ...]:
    """Close a safe Run prefix so a later user message can follow it."""

    snapshot = validate_run_messages(
        messages,
        require_final_assistant=False,
    )
    final = snapshot[-1]
    if final.role == "assistant" and not final.tool_calls:
        return snapshot
    closed = (*snapshot, Message("assistant", [TextPart(INTERRUPTED_MESSAGE)]))
    validate_run_messages(closed, require_final_assistant=True)
    return closed


def project_legacy_history(messages: Sequence[Message]) -> list[Message]:
    """Build a deterministic replay copy from legacy message-only state."""

    projected: list[Message] = []
    seen_tool_call_ids: set[str] = set()
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "system":
            index += 1
            continue
        if message.role == "tool":
            index += 1
            continue
        if message.role == "user":
            copied = deepcopy(message)
            if projected and projected[-1].role == "user":
                projected[-1] = _merge_messages(projected[-1], copied)
            else:
                projected.append(copied)
            index += 1
            continue
        if message.role != "assistant":
            index += 1
            continue
        if not message.tool_calls:
            if not _has_visible_content(message):
                index += 1
                continue
            copied = deepcopy(message)
            if (
                projected
                and projected[-1].role == "assistant"
                and not projected[-1].tool_calls
            ):
                projected[-1] = _merge_messages(projected[-1], copied)
            else:
                projected.append(copied)
            index += 1
            continue

        following_results: list[Message] = []
        next_index = index + 1
        while next_index < len(messages) and messages[next_index].role == "tool":
            following_results.append(messages[next_index])
            next_index += 1
        results_by_id: dict[str, Message] = {}
        duplicate_result_ids: set[str] = set()
        call_ids = [call.id for call in message.tool_calls]
        for result in following_results:
            result_id = result.tool_call_id
            if not result_id or result_id not in call_ids:
                continue
            if result_id in results_by_id:
                duplicate_result_ids.add(result_id)
                continue
            results_by_id[result_id] = result
        kept_calls = [
            call
            for call in message.tool_calls
            if call.id
            and call.id in results_by_id
            and call.id not in duplicate_result_ids
            and call.id not in seen_tool_call_ids
        ]
        if kept_calls:
            copied = Message(
                "assistant",
                deepcopy(message.content),
                tool_calls=deepcopy(kept_calls),
            )
            if (
                projected
                and projected[-1].role == "assistant"
                and not projected[-1].tool_calls
            ):
                copied = Message(
                    "assistant",
                    _merge_content(projected.pop(), copied),
                    tool_calls=copied.tool_calls,
                )
            projected.append(copied)
            projected.extend(
                deepcopy(results_by_id[call.id]) for call in kept_calls
            )
            seen_tool_call_ids.update(call.id for call in kept_calls)
        elif _has_visible_content(message):
            copied = Message("assistant", deepcopy(message.content))
            if (
                projected
                and projected[-1].role == "assistant"
                and not projected[-1].tool_calls
            ):
                projected[-1] = _merge_messages(projected[-1], copied)
            else:
                projected.append(copied)
        index = next_index

    while projected and projected[0].role != "user":
        projected.pop(0)
    while (
        projected
        and projected[-1].role == "user"
        and not _is_conversation_summary(projected[-1])
    ):
        projected.pop()
    return projected


def _validate_message_shape(message: Message) -> None:
    if message.role != "assistant" and message.tool_calls:
        raise MessageHistoryError("only assistant messages can contain tool calls")
    if message.role != "tool" and message.tool_call_id is not None:
        raise MessageHistoryError("only tool messages can contain tool_call_id")
    if message.role == "tool" and not message.tool_call_id:
        raise MessageHistoryError("tool result requires tool_call_id")


def _has_visible_content(message: Message) -> bool:
    return any(
        not isinstance(part, TextPart) or bool(part.text.strip())
        for part in message.content
    )


def _merge_messages(first: Message, second: Message) -> Message:
    return Message(first.role, _merge_content(first, second))


def _merge_content(first: Message, second: Message):
    separator = [TextPart("\n\n")] if first.content and second.content else []
    return [*first.content, *separator, *second.content]


def _is_conversation_summary(message: Message) -> bool:
    return any(
        isinstance(part, TextPart)
        and part.text.lstrip().startswith("<conversation_summary>")
        for part in message.content
    )
