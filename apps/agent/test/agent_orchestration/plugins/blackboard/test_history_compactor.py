import asyncio

import pytest

from apps.agent.src.agent_orchestration.plugins.blackboard import HistoryCompactor
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    LLMResponse,
    Message,
    TextPart,
    Usage,
)


class StubLLM(BaseLLM):
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False

    def invoke(self, messages, tools=None):
        raise NotImplementedError

    async def ainvoke(self, messages, tools=None):
        self.calls.append((messages, tools))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def stream(self, messages, tools=None):
        return iter(())

    async def astream(self, messages, tools=None):
        if False:
            yield

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


def test_history_compactor生成单条摘要并保留usage():
    llm = StubLLM(
        LLMResponse(
            Message("assistant", [TextPart("  保留事实  ")]),
            usage=Usage(100, 12),
        )
    )
    compactor = HistoryCompactor(llm)

    summary, usage = asyncio.run(
        compactor.compact([Message("user", [TextPart("old")])])
    )

    assert summary.content == [
        TextPart("<conversation_summary>\n保留事实\n</conversation_summary>")
    ]
    assert usage == Usage(100, 12)
    assert llm.calls[0][0][1] == Message("user", [TextPart("old")])


@pytest.mark.parametrize(
    "response",
    [
        LLMResponse(Message("assistant", [TextPart("")]), usage=Usage(1, 0)),
        LLMResponse(Message("assistant", [TextPart("summary")]), usage=None),
    ],
)
def test_history_compactor拒绝无效结果(response):
    with pytest.raises(ValueError):
        asyncio.run(HistoryCompactor(StubLLM(response)).compact([]))


def test_history_compactor关闭已经创建的llm():
    llm = StubLLM(
        LLMResponse(
            Message("assistant", [TextPart("summary")]),
            usage=Usage(1, 1),
        )
    )
    compactor = HistoryCompactor(lambda: llm)

    asyncio.run(compactor.compact([]))
    asyncio.run(compactor.aclose())

    assert llm.closed is True
