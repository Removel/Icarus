"""Pydantic models for the Gateway JSON-RPC boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.gateway_protocol.models import (
    ResourceRefModel,
    RuntimeUpdateModel,
)


JsonRpcId = str | int


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JsonRpcRequest(StrictModel):
    jsonrpc: Literal["2.0"]
    method: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    id: JsonRpcId | None = None


class JsonRpcError(StrictModel):
    code: int
    message: str
    data: dict[str, Any] | None = None


class JsonRpcResponse(StrictModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: JsonRpcId | None
    result: Any


class JsonRpcErrorResponse(StrictModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: JsonRpcId | None
    error: JsonRpcError


class JsonRpcNotification(StrictModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any]
