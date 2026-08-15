"""BaseAgent 的透明观测包装器。"""

from collections.abc import AsyncIterator, Iterator

from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.capability.types import (
    AgentCompletedEvent,
    AgentErrorEvent,
    AgentResponse,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks.hook_context import hook_context
from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import (
    HookDispatcher,
)
from apps.agent.src.model_config import LLMRole
from apps.agent.src.model_provider.types import ImagePart, Message


class ObservableAgent(BaseAgent):
    """建立一次 Agent Run 的上下文并观测调用边界。"""

    def __init__(
        self,
        agent: BaseAgent,
        dispatcher: HookDispatcher,
    ) -> None:
        self._agent = agent
        self._dispatcher = dispatcher

    @property
    def model_role(self) -> LLMRole:
        return self._agent.model_role

    def invoke(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> AgentResponse:
        with hook_context({"model_role": self.model_role}):
            self._dispatcher.trigger(
                "agent.invoke",
                "before",
                self._input_data(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                ),
            )
            try:
                response = self._agent.invoke(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                )
            except Exception as error:
                self._dispatcher.trigger(
                    "agent.invoke",
                    "error",
                    self._error_data(error),
                )
                raise
            self._dispatcher.trigger(
                "agent.invoke",
                "after",
                {"response": response},
            )
            return response

    async def ainvoke(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> AgentResponse:
        with hook_context({"model_role": self.model_role}):
            await self._dispatcher.atrigger(
                "agent.invoke",
                "before",
                self._input_data(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                ),
            )
            try:
                response = await self._agent.ainvoke(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                )
            except Exception as error:
                await self._dispatcher.atrigger(
                    "agent.invoke",
                    "error",
                    self._error_data(error),
                )
                raise
            await self._dispatcher.atrigger(
                "agent.invoke",
                "after",
                {"response": response},
            )
            return response

    def stream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> Iterator[Event]:
        with hook_context({"model_role": self.model_role}):
            self._dispatcher.trigger(
                "agent.stream",
                "before",
                self._input_data(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                ),
            )
            error_event_seen = False
            try:
                for event in self._agent.stream(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                ):
                    if isinstance(event, AgentCompletedEvent):
                        self._dispatcher.trigger(
                            "agent.stream",
                            "after",
                            {"response": event.response},
                        )
                    elif isinstance(event, AgentErrorEvent):
                        error_event_seen = True
                        self._dispatcher.trigger(
                            "agent.stream",
                            "error",
                            {
                                "error_type": event.error_type,
                                "error_message": event.error_message,
                            },
                        )
                    yield event
            except BaseException as error:
                if not error_event_seen:
                    self._dispatcher.trigger(
                        "agent.stream",
                        "error",
                        self._base_error_data(error),
                    )
                raise

    async def astream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> AsyncIterator[Event]:
        with hook_context({"model_role": self.model_role}):
            await self._dispatcher.atrigger(
                "agent.stream",
                "before",
                self._input_data(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                ),
            )
            error_event_seen = False
            try:
                async for event in self._agent.astream(
                    system_prompt,
                    history_messages,
                    input_prompt,
                    input_images,
                    tools,
                ):
                    if isinstance(event, AgentCompletedEvent):
                        await self._dispatcher.atrigger(
                            "agent.stream",
                            "after",
                            {"response": event.response},
                        )
                    elif isinstance(event, AgentErrorEvent):
                        error_event_seen = True
                        await self._dispatcher.atrigger(
                            "agent.stream",
                            "error",
                            {
                                "error_type": event.error_type,
                                "error_message": event.error_message,
                            },
                        )
                    yield event
            except BaseException as error:
                if not error_event_seen:
                    await self._dispatcher.atrigger(
                        "agent.stream",
                        "error",
                        self._base_error_data(error),
                    )
                raise

    @staticmethod
    def _input_data(
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None,
        tools: list[str] | None,
    ) -> dict[str, object]:
        return {
            "system_prompt": system_prompt,
            "history_messages": history_messages,
            "input_prompt": input_prompt,
            "input_images": input_images or [],
            "tools": tools,
        }

    @staticmethod
    def _error_data(error: Exception) -> dict[str, str]:
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }

    @staticmethod
    def _base_error_data(error: BaseException) -> dict[str, str]:
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
