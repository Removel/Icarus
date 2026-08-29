import json

import pytest

from apps.gateway.src.protocol.errors import (
    INVALID_REQUEST,
    PARSE_ERROR,
    GatewayRpcError,
)
from apps.gateway.src.protocol.jsonrpc import (
    error_response,
    parse_request,
    success_response,
)


def test_jsonrpc解析request和notification():
    request = parse_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "x"})
    )
    notification = parse_request(
        json.dumps({"jsonrpc": "2.0", "method": "x"})
    )

    assert request.id == 1
    assert "id" in request.model_fields_set
    assert "id" not in notification.model_fields_set
    assert success_response(1, {"ok": True})["result"] == {"ok": True}


@pytest.mark.parametrize(
    ("text", "code"),
    [("{", PARSE_ERROR), ("[]", INVALID_REQUEST), ("{}", INVALID_REQUEST)],
)
def test_jsonrpc非法请求返回标准错误(text, code):
    with pytest.raises(GatewayRpcError) as caught:
        parse_request(text)
    assert caught.value.code == code
    payload = error_response(None, caught.value)
    assert payload["error"]["code"] == code
    assert "traceback" not in repr(payload).lower()
