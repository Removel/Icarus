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
    assert prepared.budget.output_tokens == 25000
    assert prepared.budget.preview_tokens == 22500


@pytest.mark.parametrize(
    "window,single,batch",
    [(None, 25000, 100000), (128000, 10240, 19200), (20000, 1600, 3000)],
)
def test_execution_policy默认预算与窗口限制一致(window, single, batch):
    policy = ToolExecutionPolicy(context_window=window)

    assert policy.prepare({}).budget.output_tokens == single
    assert policy.batch_output_tokens == batch


def test_execution_policy新上限及低预算兼容():
    policy = ToolExecutionPolicy()
    assert policy.prepare({"_execution": {"max_output_tokens": 25000}}).budget.output_tokens == 25000
    assert policy.prepare({"_execution": {"max_output_tokens": 512}}).budget.output_tokens == 512
    legacy = ToolExecutionSettings(
        default_output_tokens=4000, max_output_tokens=16000,
        batch_output_tokens=16000,
    )
    assert ToolExecutionPolicy(legacy).prepare({}).budget.output_tokens == 4000
    assert ToolExecutionPolicy(legacy).batch_output_tokens == 16000


@pytest.mark.parametrize("values", [
    {"max_output_tokens": 25001},
    {"batch_output_tokens": 100001},
    {"max_output_tokens": 16000},
])
def test_tool_execution_settings拒绝越界或不兼容默认(values):
    with pytest.raises(ValueError):
        ToolExecutionSettings(**values)


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
