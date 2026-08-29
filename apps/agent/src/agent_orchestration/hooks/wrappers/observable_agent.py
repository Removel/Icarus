"""BaseAgent 的透明观测包装器。"""

from collections.abc import AsyncIterator, Iterator
import traceback
from uuid import uuid4

from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.capability.types import (
    AgentCompletedEvent,
    AgentResponse,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks.hook_context import (
    get_hook_context,
    hook_context,
)
from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import (
    HookDispatcher,
)
from apps.agent.src.agent_orchestration.run_control.types import AgentRunControl
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
        run_control: AgentRunControl | None = None,
    ) -> AgentResponse:
        with self._run_context(run_control):
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
                    run_control=run_control,
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
        run_control: AgentRunControl | None = None,
    ) -> AgentResponse:
        with self._run_context(run_control):
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
                    run_control=run_control,
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
        run_control: AgentRunControl | None = None,
    ) -> Iterator[Event]:
        context_data, run_id = self._capture_run_context(run_control)
        with hook_context(context_data, run_id=run_id):
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
        iterator = iter(
            self._agent.stream(
                system_prompt,
                history_messages,
                input_prompt,
                input_images,
                tools,
                run_control=run_control,
            )
        )
        try:
            while True:
                try:
                    with hook_context(context_data, run_id=run_id):
                        event = next(iterator)
                except StopIteration:
                    break
                with hook_context(context_data, run_id=run_id):
                    if isinstance(event, AgentCompletedEvent):
                        self._dispatcher.trigger(
                            "agent.stream",
                            "after",
                            {"response": event.response},
                        )
                yield event
        except GeneratorExit:
            raise
        except BaseException as error:
            with hook_context(context_data, run_id=run_id):
                self._dispatcher.trigger(
                    "agent.stream",
                    "error",
                    self._base_error_data(error),
                )
            raise
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                with hook_context(context_data, run_id=run_id):
                    close()

    async def astream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> AsyncIterator[Event]:
        context_data, run_id = self._capture_run_context(run_control)
        with hook_context(context_data, run_id=run_id):
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
        iterator = self._agent.astream(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
            tools,
            run_control=run_control,
        ).__aiter__()
        try:
            while True:
                try:
                    with hook_context(context_data, run_id=run_id):
                        event = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                with hook_context(context_data, run_id=run_id):
                    if isinstance(event, AgentCompletedEvent):
                        await self._dispatcher.atrigger(
                            "agent.stream",
                            "after",
                            {"response": event.response},
                        )
                yield event
        except GeneratorExit:
            raise
        except BaseException as error:
            with hook_context(context_data, run_id=run_id):
                await self._dispatcher.atrigger(
                    "agent.stream",
                    "error",
                    self._base_error_data(error),
                )
            raise
        finally:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                with hook_context(context_data, run_id=run_id):
                    await close()

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
            "traceback": "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        }

    @staticmethod
    def _base_error_data(error: BaseException) -> dict[str, str]:
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        }

    def _run_context(self, run_control: AgentRunControl | None):
        data = {"model_role": self.model_role}
        if run_control is not None and run_control.run_id is not None:
            return hook_context(data, run_id=run_control.run_id)
        return hook_context(data, new_run=True)

    def _capture_run_context(
        self, run_control: AgentRunControl | None
    ) -> tuple[dict[str, object], str]:
        parent = get_hook_context()
        data = dict(parent.data) if parent is not None else {}
        data["model_role"] = self.model_role
        run_id = (
            run_control.run_id
            if run_control is not None and run_control.run_id is not None
            else uuid4().hex
        )
        return data, run_id
