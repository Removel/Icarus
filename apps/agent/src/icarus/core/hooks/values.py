"""Rewritable call values exclude task control, routing and live resource objects."""

from dataclasses import dataclass

from icarus.core.models.messages import ImagePart, Message, TextPart, ToolCall, ToolDefinition


@dataclass(frozen=True)
class LLMCallValue:
    messages: list[Message]
    tools: list[ToolDefinition] | None


@dataclass(frozen=True)
class AgentCallValue:
    system_prompt: str
    history_messages: list[Message]
    input_prompt: str
    input_images: list[ImagePart] | None


def validate_messages(messages) -> None:
    if not isinstance(messages, list):
        raise TypeError("messages must be a list")
    for message in messages:
        if not isinstance(message, Message) or message.role not in {"system", "user", "assistant", "tool"}:
            raise TypeError("Invalid model message")
        if not isinstance(message.content, list) or any(not isinstance(part, (TextPart, ImagePart)) for part in message.content):
            raise TypeError("Invalid message content")
        if any(isinstance(part, TextPart) and not isinstance(part.text, str) for part in message.content):
            raise TypeError("Text content must be a string")
        if not isinstance(message.tool_calls, list) or any(not isinstance(call, ToolCall) for call in message.tool_calls):
            raise TypeError("Invalid tool calls")
        if any(not isinstance(call.id, str) or not isinstance(call.name, str) or not isinstance(call.arguments, dict) for call in message.tool_calls):
            raise TypeError("Invalid tool call fields")
        if message.tool_call_id is not None and not isinstance(message.tool_call_id, str):
            raise TypeError("Invalid tool result identity")


def validate_agent_call(previous: AgentCallValue, result: AgentCallValue) -> None:
    if not isinstance(result.system_prompt, str) or not isinstance(result.input_prompt, str):
        raise TypeError("Agent prompts must be strings")
    validate_messages(result.history_messages)
    if result.input_images is not None and (not isinstance(result.input_images, list) or any(not isinstance(image, ImagePart) for image in result.input_images)):
        raise TypeError("Agent images must be an image list or None")


def preserve_tool_set(previous: LLMCallValue, result: LLMCallValue) -> None:
    validate_messages(result.messages)
    if result.tools is not None and (not isinstance(result.tools, list) or any(not isinstance(tool, ToolDefinition) for tool in result.tools)):
        raise TypeError("Model tools must be a definition list or None")
    old = None if previous.tools is None else [tool.name for tool in previous.tools]
    new = None if result.tools is None else [tool.name for tool in result.tools]
    if old != new:
        raise ValueError("Hooks cannot change the available tool identities")


def message_control(message):
    return (message.role, message.tool_call_id,
            [(call.id, call.name, call.arguments) for call in message.tool_calls])


def preserve_response_control(previous, result) -> None:
    if previous.finish_reason != result.finish_reason or previous.usage != result.usage:
        raise ValueError("Hooks cannot change model control or accounting")
    validate_messages([result.message])
    if message_control(previous.message) != message_control(result.message):
        raise ValueError("Hooks cannot change message structure or tool-call identities")


def preserve_agent_control(previous, result) -> None:
    preserve_response_control(previous, result)
    for key in ("steps", "last_usage", "task_message_start"):
        if getattr(previous, key) != getattr(result, key):
            raise ValueError("Hooks cannot change agent control or accounting")
    validate_messages(result.messages)
    if len(previous.messages) != len(result.messages):
        raise ValueError("Hooks cannot change agent message structure")
    for old, new in zip(previous.messages, result.messages):
        if message_control(old) != message_control(new):
            raise ValueError("Hooks cannot change intermediate message roles or tool identities")
    if result.messages and result.messages[-1] != result.message:
        raise ValueError("Agent response and history must have the same final message")
