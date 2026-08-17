import json

from apps.agent.src.agent_orchestration.plugins import (
    BlackboardContextConverter,
    BlackboardContextReadyEvent,
    ContextBlock,
)


def test_blackboard_context_converter_稳定拍平并保持system_prompt():
    converter = BlackboardContextConverter()
    event = BlackboardContextReadyEvent(
        correlation_id="task-1",
        model_role="thinking",
        system_prompt="stable-system",
        prompt="do task",
        context_blocks=[
            ContextBlock(
                source_plugin_id="skill",
                context_type="skill",
                content="skill-b",
                metadata={"z": 2, "a": 1},
            ),
            ContextBlock(
                source_plugin_id="memory",
                context_type="memory",
                content="memory-a",
            ),
        ],
    )

    invocation = converter.convert(event)

    assert invocation.system_prompt == "stable-system"
    assert invocation.input_prompt.endswith(
        "<user_request>\ndo task\n</user_request>"
    )
    serialized = invocation.input_prompt.split(
        "<plugin_context>\n",
        1,
    )[1].split("\n</plugin_context>", 1)[0]
    blocks = json.loads(serialized)
    assert [block["source_plugin_id"] for block in blocks] == [
        "memory",
        "skill",
    ]


def test_blackboard_context_converter_失败上下文追加到当前用户输入():
    converter = BlackboardContextConverter()
    event = BlackboardContextReadyEvent(
        correlation_id="task-1",
        model_role="thinking",
        system_prompt="stable-system",
        prompt="do task",
        context_errors={"memory": "timeout"},
    )

    invocation = converter.convert(event)

    assert invocation.system_prompt == "stable-system"
    assert "<plugin_context_errors>" in invocation.input_prompt
    assert '"memory":"timeout"' in invocation.input_prompt


def test_blackboard_context_converter_优先使用blackboard已组合的input_prompt():
    converter = BlackboardContextConverter()
    event = BlackboardContextReadyEvent(
        correlation_id="task-1",
        model_role="thinking",
        system_prompt="stable-system",
        prompt="raw request",
        input_prompt="already composed",
        context_blocks=[
            ContextBlock(
                source_plugin_id="memory",
                context_type="memory",
                content="must not be composed again",
            )
        ],
    )

    invocation = converter.convert(event)

    assert invocation.input_prompt == "already composed"
