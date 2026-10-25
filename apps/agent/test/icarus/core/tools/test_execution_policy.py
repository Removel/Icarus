import pytest

from icarus.core.tools.execution_policy import (
    ToolExecutionControlError,
    ToolExecutionPolicy,
)
from agent_app.config import ToolExecutionSettings


def test_execution_policy使用稳定默认值并剥离控制参数():
    policy = ToolExecutionPolicy()

    prepared = policy.prepare({"value": 1})

    assert prepared.arguments == {"value": 1}
    assert prepared.budget.timeout_seconds == 120
    assert prepared.budget.output_tokens == 4000
    assert prepared.budget.preview_tokens == 3600


def test_execution_policy接受范围内申请并受模型窗口收紧():
    policy = ToolExecutionPolicy(context_window=20_000)

    prepared = policy.prepare(
        {
            "value": 1,
            "_execution": {
                "timeout_seconds": 300,
                "max_output_tokens": 8000,
            },
        }
    )

    assert prepared.arguments == {"value": 1}
    assert prepared.request.timeout_seconds == 300
    assert prepared.budget.output_tokens == 1600
    assert policy.batch_output_tokens == 3000


@pytest.mark.parametrize(
    "control",
    [
        "bad",
        {"unknown": 1},
        {"timeout_seconds": True},
        {"timeout_seconds": 601},
        {"max_output_tokens": 511},
        {"max_output_tokens": 25001},
    ],
)
def test_execution_policy拒绝非法控制参数(control):
    with pytest.raises(ToolExecutionControlError):
        ToolExecutionPolicy().prepare({"_execution": control})


@pytest.mark.parametrize("context_window, single, batch", [
    (128_000, 10_240, 19_200),
    (1_000_000, 25_000, 100_000),
])
def test_execution_policy提高上限但保留窗口比例(context_window, single, batch):
    from jsonschema import Draft202012Validator

    policy = ToolExecutionPolicy(context_window=context_window)
    schema = policy.control_schema()
    assert schema["properties"]["max_output_tokens"]["maximum"] == 25_000
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"max_output_tokens": 25_000})
    assert not validator.is_valid({"max_output_tokens": 25_001})
    assert not validator.is_valid({"unexpected": 1})
    prepared = policy.prepare({"_execution": {"max_output_tokens": 25_000}})
    assert prepared.budget.output_tokens == single
    assert prepared.budget.preview_tokens == int(single * 0.9)
    assert policy.batch_output_tokens == batch


def test_execution_policy分配不超组预算且小结果不填充():
    from icarus.core.models.messages import ToolCall
    from icarus.core.tools.tool_executor import _allocate_group_tokens

    policy = ToolExecutionPolicy(context_window=1_000_000)
    calls = [ToolCall(str(index), "example", {}) for index in range(8)]
    allocations = _allocate_group_tokens(
        calls,
        {call.id: 100 if call.id == "0" else 30_000 for call in calls},
        {call.id: 25_000 for call in calls},
        policy.batch_output_tokens,
        policy.settings.min_output_tokens,
    )
    assert allocations["0"] == 100
    assert sum(allocations.values()) <= 100_000
    assert all(value <= 25_000 for value in allocations.values())


@pytest.mark.parametrize('field,value', [
    ('max_output_tokens', 25001),
    ('max_output_tokens', 50000),
    ('batch_output_tokens', 100001),
    ('batch_output_tokens', 200000),
])
def test_tool_execution_settings_reject_configured_limits_above_absolute_caps(field, value):
    with pytest.raises(ValueError, match=field):
        ToolExecutionSettings(**{field: value})


@pytest.mark.parametrize('context_window,single,batch', [
    (1_000_000, 12000, 40000),
    (20_000, 1600, 3000),
])
def test_lower_configured_caps_remain_valid_and_context_window_can_shrink_them(context_window, single, batch):
    policy = ToolExecutionPolicy(ToolExecutionSettings(max_output_tokens=12000, batch_output_tokens=40000),
        context_window=context_window)
    prepared = policy.prepare({'_execution': {'max_output_tokens': 12000}})
    assert prepared.budget.output_tokens == single
    assert policy.batch_output_tokens == batch
    assert policy.control_schema()['properties']['max_output_tokens']['maximum'] == 12000
    with pytest.raises(ToolExecutionControlError):
        policy.prepare({'_execution': {'max_output_tokens': 12001}})


def test_tool_execution_settings拒绝无法覆盖最小batch的配置():
    with pytest.raises(ValueError, match="batch_output_tokens"):
        ToolExecutionSettings(
            min_output_tokens=1000,
            max_calls_per_batch=8,
            batch_output_tokens=7000,
        )


@pytest.mark.parametrize(
    "field",
    [
        "default_timeout_seconds",
        "max_timeout_seconds",
        "max_calls_per_batch",
        "preview_target_ratio",
        "head_ratio",
        "default_page_size",
    ],
)
def test_tool_execution_settings拒绝bool伪装成数值(field):
    with pytest.raises(ValueError):
        ToolExecutionSettings(**{field: True})


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_tool_execution_settings拒绝非有限超时(value):
    with pytest.raises(ValueError):
        ToolExecutionSettings(
            default_timeout_seconds=value, max_timeout_seconds=value
        )
