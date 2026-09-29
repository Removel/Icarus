import asyncio

from apps.argus.src.queue import InboundQueue
from apps.argus.src.worker import QueueWorker
from apps.argus.src.handler.QQHandler import QQHandler


def test_worker_processes_observation_and_finishes_queue_item():
    async def exercise():
        seen = []
        queue = InboundQueue(2)
        worker = QueueWorker(queue, process=lambda obs: capture(obs, seen))
        obs = QQHandler("10000").parse({
            "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": 300,
            "message_type": "private", "user_id": 7, "message": "你好",
        })
        worker.start()
        queue.put_nowait(obs)
        await asyncio.wait_for(queue.join(), 1)
        assert seen == [obs.id]
        await worker.stop()

    async def capture(obs, seen):
        seen.append(obs.id)

    asyncio.run(exercise())


def test_worker_starts_and_stops_twice():
    async def exercise():
        worker = QueueWorker(InboundQueue(1))
        assert not worker.is_running
        worker.start()
        assert worker.is_running
        await worker.stop()
        assert not worker.is_running
        await worker.stop()

    asyncio.run(exercise())


def test_cancelled_worker_is_not_running():
    async def exercise():
        worker = QueueWorker(InboundQueue(1))
        worker.start()
        worker.task.cancel()
        await asyncio.gather(worker.task, return_exceptions=True)
        assert not worker.is_running
        await worker.stop()

    asyncio.run(exercise())
