"""Explicit JSON-RPC method adapters for AgentRuntime."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from apps.agent.src.application import (
    AgentRuntime,
    AgentRuntimeStoppingError,
    ConversationHistoryCorruptError,
    InvalidResourceError,
    ResourceRef,
    ResourceUnavailableError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SubmissionConflictError,
)
from apps.gateway.src.protocol.errors import (
    BUSINESS_ERROR,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    GatewayRpcError,
)
from apps.gateway.src.protocol.models import (
    ResourceRefModel,
    RuntimeUpdateModel,
    StrictModel,
)
from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    SessionListModel,
    SessionSummaryModel,
)


class WorkspaceParams(StrictModel):
    workspace_path: str = Field(min_length=1, pattern=r".*\S.*")


class SessionParams(WorkspaceParams):
    session_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")


class CreateSessionParams(WorkspaceParams):
    session_id: str | None = Field(
        default=None, min_length=1, pattern=r"^[A-Za-z0-9._-]+$"
    )


class SubmitParams(SessionParams):
    prompt: str
    display_text: str | None = None
    submission_id: str = Field(min_length=1, pattern=r".*\S.*")
    resources: tuple[ResourceRefModel, ...] = ()


class CancelParams(SessionParams):
    task_id: str
    reason: str | None = None


class TaskParams(SessionParams):
    task_id: str


class HistoryParams(SessionParams):
    after_sequence: int = Field(default=0, ge=0)


class SubscriptionParams(StrictModel):
    workspace_key: str
    session_id: str


class GatewayMethods:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.runtime = runtime
        self.logger = logger or logging.getLogger("icarus.gateway.methods")

    async def dispatch(
        self,
        method: str,
        params: dict[str, Any],
        subscriptions: set[tuple[str, str]],
    ) -> Any:
        handlers = {
            "runtime.get_status": self._runtime_status,
            "session.create": self._session_create,
            "session.list": self._session_list,
            "session.discard_empty": self._session_discard_empty,
            "session.get": self._session_get,
            "session.submit": self._session_submit,
            "session.cancel": self._session_cancel,
            "session.unload": self._session_unload,
            "session.get_history": self._session_history,
            "task.get_status": self._task_get,
        }
        if method == "session.subscribe":
            value = self._validate(SubscriptionParams, params)
            subscriptions.add((value.workspace_key, value.session_id))
            return {"subscribed": True}
        if method == "session.unsubscribe":
            value = self._validate(SubscriptionParams, params)
            subscriptions.discard((value.workspace_key, value.session_id))
            return {"subscribed": False}
        handler = handlers.get(method)
        if handler is None:
            raise GatewayRpcError(METHOD_NOT_FOUND, "Method not found")
        try:
            return await handler(params)
        except GatewayRpcError:
            raise
        except SessionNotFoundError as error:
            raise _business("session_not_found", "Session is not found") from error
        except SessionAlreadyExistsError as error:
            raise _business("session_exists", "Session already exists") from error
        except SubmissionConflictError as error:
            raise _business("submission_conflict", "Submission ID conflicts with another request") from error
        except AgentRuntimeStoppingError as error:
            raise _business("runtime_stopping", "Runtime is not accepting calls") from error
        except ConversationHistoryCorruptError as error:
            raise _business(
                "history_corrupt", "Session history is corrupt"
            ) from error
        except KeyError as error:
            raise _business("task_status_unavailable", "Task status is unavailable") from error
        except Exception as error:
            self.logger.exception("Gateway method failed: method=%s", method)
            raise GatewayRpcError(INTERNAL_ERROR, "Internal error") from error

    async def _runtime_status(self, params):
        self._validate(StrictModel, params)
        return {"status": "ready" if self.runtime.is_running else "stopped"}

    async def _session_create(self, params):
        value = self._validate(CreateSessionParams, params)
        session_id = await self.runtime.create_session(
            value.workspace_path, value.session_id
        )
        return _wire(
            await self.runtime.get_session_status(value.workspace_path, session_id)
        )

    async def _session_list(self, params):
        value = self._validate(WorkspaceParams, params)
        result = SessionListModel(
            sessions=tuple(
                SessionSummaryModel.from_domain(item)
                for item in await self.runtime.list_session_summaries(
                    value.workspace_path
                )
            )
        )
        return result.model_dump(mode="json")

    async def _session_discard_empty(self, params):
        value = self._validate(SessionParams, params)
        result = await self.runtime.discard_empty_session(
            value.workspace_path, value.session_id
        )
        return DiscardEmptySessionResultModel.from_domain(result).model_dump(
            mode="json"
        )

    async def _session_get(self, params):
        value = self._validate(SessionParams, params)
        return _wire(
            await self.runtime.get_session_status(
                value.workspace_path, value.session_id
            )
        )

    async def _session_submit(self, params):
        value = self._validate(SubmitParams, params)
        try:
            resources = tuple(
                ResourceRef(item.resource_id, item.media_type)
                for item in value.resources
            )
            accepted = await self.runtime.submit(
                value.workspace_path,
                value.session_id,
                value.prompt,
                submission_id=value.submission_id,
                resources=resources,
                display_text=value.display_text,
            )
        except ResourceUnavailableError as error:
            raise _business(
                "resource_unavailable", "Resource is unavailable"
            ) from error
        except InvalidResourceError as error:
            raise _business("invalid_resource", "Resource is invalid") from error
        return _wire(accepted)

    async def _session_cancel(self, params):
        value = self._validate(CancelParams, params)
        return _wire(
            await self.runtime.cancel_task(
                value.workspace_path,
                value.session_id,
                value.task_id,
                value.reason,
            )
        )

    async def _session_unload(self, params):
        value = self._validate(SessionParams, params)
        return _wire(
            await self.runtime.unload_session(
                value.workspace_path, value.session_id
            )
        )

    async def _task_get(self, params):
        value = self._validate(TaskParams, params)
        return _wire(
            self.runtime.get_task_status(
                value.workspace_path, value.session_id, value.task_id
            )
        )

    async def _session_history(self, params):
        value = self._validate(HistoryParams, params)
        records, cursor = await self.runtime.get_session_history(
            value.workspace_path,
            value.session_id,
            after_sequence=value.after_sequence,
        )
        return {
            "records": [
                RuntimeUpdateModel.from_domain(item).model_dump(mode="json")
                for item in records
            ],
            "history_cursor": cursor,
        }

    @staticmethod
    def _validate(model, params):
        try:
            return model.model_validate(params)
        except ValidationError as error:
            raise GatewayRpcError(INVALID_PARAMS, "Invalid params") from error


def _business(code: str, message: str) -> GatewayRpcError:
    return GatewayRpcError(BUSINESS_ERROR, message, data={"code": code})


def _wire(value):
    if is_dataclass(value):
        return _wire(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value
