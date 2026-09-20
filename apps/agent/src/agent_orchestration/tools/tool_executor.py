"""工具执行入口。"""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
import logging
import inspect
import time

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.execution_policy import (
    PreparedToolArguments,
    ToolExecutionControlError,
    ToolContextBudgetExceededError,
    ToolExecutionPolicy,
)
from apps.agent.src.agent_orchestration.tools.result_budget import (
    TokenCounter,
    conservative_token_count,
    render_tool_result,
    serialize_tool_result,
    token_counter_with_fallback,
)
from apps.agent.src.agent_orchestration.tools.result_store import ToolResultStore
from apps.agent.src.agent_orchestration.tools.tool_registry import ToolRegistry
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import Message, ToolCall, ToolDefinition


logger = logging.getLogger(__name__)
ToolResultPair = tuple[ToolCall, ToolExecutionResult]


class BaseToolExecutor(ABC):
    """Agent 能力层依赖的工具执行契约。"""

    @abstractmethod
    def definitions(
        self,
        names: list[str] | None = None,
    ) -> list[ToolDefinition]:
        ...

    @abstractmethod
    def execute(
        self,
        tool_call: ToolCall,
        **execution: object,
    ) -> ToolExecutionResult:
        ...

    @abstractmethod
    async def aexecute(
        self,
        tool_call: ToolCall,
        **execution: object,
    ) -> ToolExecutionResult:
        ...

    def snapshot(self, names: list[str] | None = None) -> "BaseToolExecutor":
        return self

    @abstractmethod
    def can_run_parallel(self, tool_call: ToolCall) -> bool:
        ...

    def build_batches(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        batches: list[list[ToolCall]] = []
        parallel_batch: list[ToolCall] = []

        for tool_call in tool_calls:
            if self.can_run_parallel(tool_call):
                parallel_batch.append(tool_call)
                continue

            if parallel_batch:
                batches.append(parallel_batch)
                parallel_batch = []
            batches.append([tool_call])

        if parallel_batch:
            batches.append(parallel_batch)
        return batches

    def prepare_group(
        self, tool_calls: list[ToolCall]
    ) -> tuple[list[ToolCall], dict[str, ToolExecutionResult]]:
        return tool_calls, {}

    def finalize_group(
        self,
        tool_calls: list[ToolCall],
        results_by_id: dict[str, ToolExecutionResult],
        **execution: object,
    ) -> dict[str, ToolExecutionResult]:
        del tool_calls, execution
        return results_by_id

    def iter_completed(
        self,
        tool_calls: list[ToolCall],
        **execution: object,
    ) -> Iterator[ToolResultPair]:
        if len(tool_calls) <= 1:
            for tool_call in tool_calls:
                yield tool_call, self.execute(tool_call, **execution)
            return

        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            futures = {
                executor.submit(
                    copy_context().run,
                    self.execute,
                    tool_call,
                    **execution,
                ): tool_call
                for tool_call in tool_calls
            }
            for future in as_completed(futures):
                yield futures[future], future.result()

    async def aiter_completed(
        self,
        tool_calls: list[ToolCall],
        **execution: object,
    ) -> AsyncIterator[ToolResultPair]:
        async def execute_pair(tool_call: ToolCall) -> ToolResultPair:
            return tool_call, await self.aexecute(tool_call, **execution)

        tasks = [
            asyncio.create_task(execute_pair(tool_call))
            for tool_call in tool_calls
        ]
        try:
            for task in asyncio.as_completed(tasks):
                yield await task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def execute_many(
        self,
        tool_calls: list[ToolCall],
        **execution: object,
    ) -> list[ToolResultPair]:
        executable, results_by_id = self.prepare_group(tool_calls)
        for batch in self.build_batches(executable):
            results_by_id.update(
                {
                    tool_call.id: result
                    for tool_call, result in self.iter_completed(
                        batch, **execution
                    )
                }
            )
        finalized = self.finalize_group(
            tool_calls, results_by_id, **execution
        )
        return [(tool_call, finalized[tool_call.id]) for tool_call in tool_calls]

    async def aexecute_many(
        self,
        tool_calls: list[ToolCall],
        **execution: object,
    ) -> list[ToolResultPair]:
        executable, results_by_id = self.prepare_group(tool_calls)
        for batch in self.build_batches(executable):
            async for tool_call, result in self.aiter_completed(
                batch, **execution
            ):
                results_by_id[tool_call.id] = result
        finalized = self.finalize_group(
            tool_calls, results_by_id, **execution
        )
        return [(tool_call, finalized[tool_call.id]) for tool_call in tool_calls]


class ToolExecutor(BaseToolExecutor):
    """查找并执行工具，将所有路径统一为 ToolExecutionResult。"""

    ASYNC_CANCEL_GRACE_SECONDS = 1.0

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        policy: ToolExecutionPolicy | None = None,
        result_store: ToolResultStore | None = None,
        token_counter: TokenCounter = conservative_token_count,
    ) -> None:
        self.registry = registry
        self.policy = policy or ToolExecutionPolicy()
        self.result_store = result_store
        self.token_counter = token_counter_with_fallback(token_counter)

    def definitions(
        self,
        names: list[str] | None = None,
    ) -> list[ToolDefinition]:
        definitions = deepcopy(self.registry.definitions(names))
        control_schema = self.policy.control_schema()
        return [
            ToolDefinition(
                definition.name,
                definition.description,
                self._with_execution_schema(
                    definition.input_schema, control_schema
                ),
            )
            for definition in definitions
        ]

    def snapshot(self, names: list[str] | None = None) -> BaseToolExecutor:
        registry = ToolRegistry()
        registry.register_many(self.registry.select(names))
        registry.freeze()
        return ToolExecutor(
            registry,
            policy=self.policy,
            result_store=self.result_store,
            token_counter=self.token_counter,
        )

    def prepare_group(
        self, tool_calls: list[ToolCall]
    ) -> tuple[list[ToolCall], dict[str, ToolExecutionResult]]:
        settings = self.policy.settings
        batch_output_tokens = self.policy.batch_output_tokens
        if len(tool_calls) * settings.min_output_tokens > batch_output_tokens:
            raise ToolContextBudgetExceededError(
                "Tool Group cannot fit minimum Tool Result envelopes within "
                f"the {batch_output_tokens}-token batch budget"
            )
        executable = tool_calls[: settings.max_calls_per_batch]
        rejected = {
            call.id: ToolExecutionResult(
                False,
                error=(
                    "Tool call was not started because this Tool Group exceeds "
                    f"the {settings.max_calls_per_batch}-call limit"
                ),
                metadata={"disposition": "budget_exhausted"},
            )
            for call in tool_calls[settings.max_calls_per_batch :]
        }
        return executable, rejected

    def finalize_group(
        self,
        tool_calls: list[ToolCall],
        results_by_id: dict[str, ToolExecutionResult],
        **execution: object,
    ) -> dict[str, ToolExecutionResult]:
        settings = self.policy.settings
        batch_output_tokens = self.policy.batch_output_tokens
        task_id = execution.get("task_id")
        sizes = {
            call.id: self.token_counter(serialize_tool_result(results_by_id[call.id]))
            for call in tool_calls
        }
        caps = {
            call.id: int(
                results_by_id[call.id].metadata.get(
                    "effective_output_tokens", settings.default_output_tokens
                )
            )
            for call in tool_calls
        }
        allocations = _allocate_group_tokens(
            tool_calls,
            sizes,
            caps,
            batch_output_tokens,
            settings.min_output_tokens,
        )
        finalized: dict[str, ToolExecutionResult] = {}
        for call in tool_calls:
            result = results_by_id[call.id]
            budget = allocations[call.id]
            stored = None
            if sizes[call.id] > budget:
                stored_result = ToolExecutionResult(
                    result.success,
                    result.output,
                    result.error,
                    result.images,
                )
                pretty_result = serialize_tool_result(stored_result, pretty=True)
                result = ToolExecutionResult(
                    result.success,
                    result.output,
                    result.error,
                    result.images,
                    {
                        **result.metadata,
                        "original_bytes": len(pretty_result.encode("utf-8")),
                    },
                )
                if self.result_store is not None and isinstance(task_id, str):
                    try:
                        stored = self.result_store.save(
                            task_id=task_id,
                            tool_call_id=call.id,
                            content=pretty_result,
                            source_complete=not bool(
                                result.metadata.get("output_capture_incomplete")
                            ),
                        )
                    except (OSError, ValueError) as error:
                        logger.warning(
                            "Tool Result file write failed: call_id=%s error=%s",
                            call.id, error,
                        )
                        result = self._with_result_file_error(result, error)
                else:
                    result = self._with_result_file_error(
                        result, RuntimeError("Session Tool Result store unavailable")
                    )
            rendered = render_tool_result(
                result,
                max_tokens=budget,
                preview_tokens=max(1, int(budget * settings.preview_target_ratio)),
                counter=self.token_counter,
                stored=stored,
                head_ratio=settings.head_ratio,
            )
            if self.token_counter(serialize_tool_result(rendered)) > budget:
                raise ToolContextBudgetExceededError(
                    f"Tool Result cannot fit budget: call_id={call.id}"
                )
            finalized[call.id] = rendered
        if sum(
            self.token_counter(serialize_tool_result(finalized[call.id]))
            for call in tool_calls
        ) > batch_output_tokens:
            raise ToolContextBudgetExceededError(
                "Tool Group cannot fit the batch output budget"
            )
        return finalized

    @staticmethod
    def _with_result_file_error(
        result: ToolExecutionResult, error: Exception
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            result.success,
            result.output,
            result.error,
            result.images,
            {
                **result.metadata,
                "result_file_error": (
                    f"{type(error).__name__}: {error}"
                ),
            },
        )

    def execute(
        self,
        tool_call: ToolCall,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        try:
            prepared = self.policy.prepare(tool_call.arguments)
        except ToolExecutionControlError as error:
            return self._control_error_result(error)
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return self._with_budget_metadata(
                ToolExecutionResult(
                    success=False,
                    error=f"Tool is not registered: {tool_call.name}",
                ),
                prepared,
                0.0,
            )

        started_at = time.monotonic()
        try:
            execution = _supported_execution_arguments(
                tool.invoke,
                task_id=task_id,
                run_id=run_id,
                step=step,
                task_messages=_copy_messages(task_messages),
                timeout_seconds=prepared.budget.timeout_seconds,
            )
            result = tool.invoke(prepared.arguments, **execution)
        except Exception as error:
            logger.exception(
                "Tool execution failed: name=%s call_id=%s",
                tool_call.name,
                tool_call.id,
            )
            result = ToolExecutionResult(success=False, error=str(error))

        return self._with_budget_metadata(
            self._normalize_result(tool_call, result),
            prepared,
            time.monotonic() - started_at,
        )

    async def aexecute(
        self,
        tool_call: ToolCall,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        try:
            prepared = self.policy.prepare(tool_call.arguments)
        except ToolExecutionControlError as error:
            return self._control_error_result(error)
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return self._with_budget_metadata(
                ToolExecutionResult(
                    success=False,
                    error=f"Tool is not registered: {tool_call.name}",
                ),
                prepared,
                0.0,
            )

        started_at = time.monotonic()
        try:
            use_sync_implementation = type(tool).ainvoke is BaseTool.ainvoke
            target = tool.invoke if use_sync_implementation else tool.ainvoke
            execution = _supported_execution_arguments(
                target,
                task_id=task_id,
                run_id=run_id,
                step=step,
                task_messages=_copy_messages(task_messages),
                timeout_seconds=prepared.budget.timeout_seconds,
            )
            if use_sync_implementation:
                result = await asyncio.to_thread(
                    tool.invoke, prepared.arguments, **execution
                )
            else:
                task = asyncio.create_task(
                    tool.ainvoke(prepared.arguments, **execution)
                )
                try:
                    done, _ = await asyncio.wait(
                        {task}, timeout=prepared.budget.timeout_seconds
                    )
                    if task in done:
                        result = task.result()
                    else:
                        cancellation_confirmed = await self._cancel_async_tool(task)
                        result = ToolExecutionResult(
                            success=False,
                            error=(
                                "Tool execution timed out after "
                                f"{prepared.budget.timeout_seconds:g} seconds"
                            ),
                            metadata={
                                "disposition": "timed_out",
                                "cancellation_confirmed": cancellation_confirmed,
                            },
                        )
                except asyncio.CancelledError:
                    await asyncio.shield(self._cancel_async_tool(task))
                    raise
        except Exception as error:
            logger.exception(
                "Async tool execution failed: name=%s call_id=%s",
                tool_call.name,
                tool_call.id,
            )
            result = ToolExecutionResult(success=False, error=str(error))

        return self._with_budget_metadata(
            self._normalize_result(tool_call, result),
            prepared,
            time.monotonic() - started_at,
        )

    def can_run_parallel(self, tool_call: ToolCall) -> bool:
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return False
        try:
            arguments = self.policy.prepare(tool_call.arguments).arguments
        except ToolExecutionControlError:
            return False
        return tool.can_run_parallel(arguments)

    @staticmethod
    def _with_execution_schema(schema, control_schema):
        copied = deepcopy(schema)
        properties = copied.setdefault("properties", {})
        properties["_execution"] = deepcopy(control_schema)
        return copied

    @staticmethod
    def _with_budget_metadata(
        result: ToolExecutionResult,
        prepared: PreparedToolArguments,
        duration: float,
    ) -> ToolExecutionResult:
        metadata = {
            **result.metadata,
            "disposition": result.metadata.get(
                "disposition", "completed" if result.success else "failed"
            ),
            "requested_timeout_seconds": prepared.request.timeout_seconds,
            "requested_output_tokens": prepared.request.max_output_tokens,
            "effective_timeout_seconds": prepared.budget.timeout_seconds,
            "effective_output_tokens": prepared.budget.output_tokens,
            "preview_tokens": prepared.budget.preview_tokens,
            "duration_seconds": duration,
        }
        return ToolExecutionResult(
            result.success, result.output, result.error, result.images, metadata
        )

    def _control_error_result(
        self, error: ToolExecutionControlError
    ) -> ToolExecutionResult:
        settings = self.policy.settings
        output_tokens = settings.default_output_tokens
        if self.policy.context_window is not None:
            output_tokens = min(
                output_tokens, max(1, int(self.policy.context_window * 0.08))
            )
        return ToolExecutionResult(
            success=False,
            error=str(error),
            metadata={
                "disposition": "invalid_execution_control",
                "effective_timeout_seconds": settings.default_timeout_seconds,
                "effective_output_tokens": output_tokens,
                "preview_tokens": max(
                    1, int(output_tokens * settings.preview_target_ratio)
                ),
                "duration_seconds": 0.0,
            },
        )

    async def _cancel_async_tool(self, task: asyncio.Task) -> bool:
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self.ASYNC_CANCEL_GRACE_SECONDS
            )
        except asyncio.CancelledError:
            return True
        except TimeoutError:
            task.add_done_callback(_consume_task_result)
            return False
        except Exception:
            return True
        return task.done()

    @staticmethod
    def _normalize_result(
        tool_call: ToolCall,
        result: object,
    ) -> ToolExecutionResult:
        if isinstance(result, ToolExecutionResult):
            return result
        return ToolExecutionResult(
            success=False,
            error=(
                f"Tool returned invalid result: name={tool_call.name} "
                f"type={type(result).__name__}"
            ),
        )


def _copy_messages(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    return tuple(deepcopy(messages))


def _supported_execution_arguments(callable_, **execution: object) -> dict:
    parameters = inspect.signature(callable_).parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_kwargs:
        return execution
    return {
        name: value
        for name, value in execution.items()
        if name in parameters
    }


def _allocate_group_tokens(tool_calls, sizes, caps, total, minimum):
    desired = {
        call.id: max(1, min(sizes[call.id], caps[call.id]))
        for call in tool_calls
    }
    if sum(desired.values()) <= total:
        return desired

    allocations = {
        call.id: min(desired[call.id], minimum) for call in tool_calls
    }
    remaining = total - sum(allocations.values())
    pending = [
        call.id for call in tool_calls if allocations[call.id] < desired[call.id]
    ]
    while pending and remaining > 0:
        share = max(1, remaining // len(pending))
        progressed = False
        for call_id in tuple(pending):
            added = min(share, desired[call_id] - allocations[call_id], remaining)
            if added > 0:
                allocations[call_id] += added
                remaining -= added
                progressed = True
            if allocations[call_id] >= desired[call_id]:
                pending.remove(call_id)
        if not progressed:
            break
    return allocations


def _consume_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except BaseException:
        pass
