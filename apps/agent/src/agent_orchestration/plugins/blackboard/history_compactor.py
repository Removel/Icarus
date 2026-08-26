"""使用模型压缩 Blackboard 当前有效历史。"""

from collections.abc import Callable

from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import Message, TextPart, Usage


COMPACT_SYSTEM_PROMPT = """你负责压缩一段对话历史，以便另一个模型只阅读摘要也能继续当前工作。
只输出一份自包含摘要，不要回答用户，不要继续执行任务。
保留用户目标、明确约束、已作决定、已验证事实、重要路径与接口、关键错误、当前状态和未完成事项。
对已经从图片中得到的重要信息只保留文字事实，不保留 [image#N] 等展示编号。
保留后续操作需要精确复用的名称、数值和字符串。
不要添加原历史中不存在的事实、推断或建议。"""


class HistoryCompactor:
    def __init__(self, llm: BaseLLM | Callable[[], BaseLLM]) -> None:
        self._llm_or_factory = llm
        self._llm: BaseLLM | None = llm if isinstance(llm, BaseLLM) else None

    @property
    def llm(self) -> BaseLLM:
        if self._llm is None:
            factory = self._llm_or_factory
            if not callable(factory):
                raise RuntimeError("history compactor LLM is unavailable")
            self._llm = factory()
        return self._llm

    async def compact(
        self, messages: list[Message]
    ) -> tuple[Message, Usage]:
        response = await self.llm.ainvoke(
            [Message("system", [TextPart(COMPACT_SYSTEM_PROMPT)]), *messages],
            None,
        )
        text = "".join(
            part.text
            for part in response.message.content
            if isinstance(part, TextPart)
        ).strip()
        if not text:
            raise ValueError("compact response is empty")
        if response.usage is None:
            raise ValueError("compact response has no usage")
        return (
            Message(
                "user",
                [TextPart(f"<conversation_summary>\n{text}\n</conversation_summary>")],
            ),
            response.usage,
        )

    async def aclose(self) -> None:
        if self._llm is not None:
            await self._llm.aclose()
