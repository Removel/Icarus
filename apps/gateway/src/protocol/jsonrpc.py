"""Small JSON-RPC 2.0 parser and response helpers."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from apps.gateway.src.protocol.errors import (
    INVALID_REQUEST,
    PARSE_ERROR,
    GatewayRpcError,
)
from apps.gateway.src.protocol.models import (
    JsonRpcError,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcResponse,
)


def parse_request(text: str) -> JsonRpcRequest:
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as error:
        raise GatewayRpcError(PARSE_ERROR, "Parse error") from error
    if not isinstance(payload, dict):
        raise GatewayRpcError(INVALID_REQUEST, "Invalid Request")
    try:
        return JsonRpcRequest.model_validate(payload)
    except ValidationError as error:
        raise GatewayRpcError(INVALID_REQUEST, "Invalid Request") from error


def success_response(request_id, result) -> dict[str, Any]:
    return JsonRpcResponse(id=request_id, result=result).model_dump(
        mode="json"
    )


def error_response(request_id, error: GatewayRpcError) -> dict[str, Any]:
    return JsonRpcErrorResponse(
        id=request_id,
        error=JsonRpcError(
            code=error.code, message=error.message, data=error.data
        ),
    ).model_dump(mode="json", exclude_none=True)
