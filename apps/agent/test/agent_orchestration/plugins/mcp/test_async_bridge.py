import asyncio
import threading

import pytest

from apps.agent.src.agent_orchestration.plugins.mcp.async_bridge import (
    MCPAsyncBridge,
)


def test_bridge同步和异步调用都运行在同一个专用线程():
    bridge = MCPAsyncBridge()
    bridge.start()

    async def identify():
        return threading.get_ident(), id(asyncio.get_running_loop())

    try:
        sync_identity = bridge.run(identify)
        async_identity = asyncio.run(bridge.arun(identify))
    finally:
        bridge.stop()

    assert sync_identity == async_identity
    assert sync_identity[0] != threading.get_ident()


def test_bridge停止后拒绝调用():
    bridge = MCPAsyncBridge()
    bridge.start()
    bridge.stop()

    async def value():
        return 1

    with pytest.raises(RuntimeError, match="not running"):
        bridge.run(value)
