import asyncio

from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.queue import InboundQueue
from apps.argus.src.worker import recover_pending


def test_recovery_fills_queue_in_pages_and_preserves_qq_type(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        handler = QQHandler("10000")
        for message_id in (301, 302):
            await store.insert(handler.parse({
                "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": message_id,
                "message_type": "private", "user_id": 7, "message": "你好",
            }))
        queue = InboundQueue(1)
        scheduled = set()
        assert await recover_pending(store, queue, scheduled) == 1
        assert await recover_pending(store, queue, scheduled) == 0
        first = await queue.get()
        queue.task_done()
        scheduled.remove(first.id)
        assert await store.claim(first.id)
        assert await recover_pending(store, queue, scheduled) == 1
        second = await queue.get()
        assert second.source.source_message_id != first.source.source_message_id
        assert second.__class__.__name__ == "QQObservation"
        queue.task_done()
        await store.close()

    asyncio.run(exercise())


def test_restart_requeues_processing_without_submission_intent(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = QQHandler("10000").parse({
            "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": 303,
            "message_type": "private", "user_id": 7, "message": "你好",
        })
        await store.insert(obs)
        assert await store.claim(obs.id)
        await store.close()
        reopened = ObservationStore(tmp_path)
        await reopened.start()
        await reopened.reset_interrupted()
        queue = InboundQueue(1)
        assert await recover_pending(reopened, queue, set()) == 1
        restored = await queue.get()
        assert restored.id == obs.id
        queue.task_done()
        await reopened.close()

    asyncio.run(exercise())

def test_only_one_claim_succeeds(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = QQHandler("10000").parse({
            "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": 300,
            "message_type": "private", "user_id": 7, "message": "你好",
        })
        await store.insert(obs)
        assert await store.claim(obs.id)
        assert not await store.claim(obs.id)
        await store.close()

    asyncio.run(exercise())
