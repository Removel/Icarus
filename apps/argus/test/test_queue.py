import asyncio

import pytest

from apps.argus.src.queue import InboundQueue


def test_full_queue_raises():
    queue = InboundQueue(1)
    first = object()
    queue.put_nowait(first)

    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait(object())

    assert asyncio.run(queue.get()) is first
    queue.task_done()
