import asyncio
from pathlib import Path

import pytest

from apps.argus.src.dao.observation_store import ObservationConflictError, ObservationStore
from apps.argus.src.entity.qq_observation import QQObservation
from apps.argus.src.handler.qq_handler import QQHandler


def observation(message_id=301, group_id=42, message="你好", self_id=10000):
    return QQHandler(str(self_id)).parse({
        "post_type": "message", "self_id": self_id, "message_id": message_id,
        "message_type": "group", "group_id": group_id, "user_id": 7,
        "time": 1700000000,
        "message": [
            {"type": "text", "data": {"text": message}},
            {"type": "image", "data": {"file": "a.png"}},
        ],
    })


def test_roundtrip_and_exact_source_deduplication(tmp_path: Path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        first = observation()
        assert await store.insert(first)
        assert not await store.insert(observation())
        assert len(await store.recent("qq", "10000", "qq:group:42")) == 1
        await store.close()
        reopened = ObservationStore(tmp_path)
        await reopened.start()
        restored = await reopened.get(first.id)
        assert isinstance(restored, QQObservation)
        assert restored == first
        await reopened.close()

    asyncio.run(exercise())
    assert (tmp_path / "argus.db").is_file()


def test_same_source_id_with_different_content_is_conflict(tmp_path: Path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        first = observation()
        assert await store.insert(first)
        with pytest.raises(ObservationConflictError):
            await store.insert(observation(message="不同的内容"))
        assert await store.get(first.id) == first
        await store.close()

    asyncio.run(exercise())


def test_same_payload_with_source_time_is_idempotent(tmp_path: Path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        raw = {
            "post_type": "message", "self_id": 10000, "message_id": 999,
            "message_type": "private", "user_id": 7, "message": "你好",
            "time": 1700000000,
        }
        handler = QQHandler("10000")
        assert await store.insert(handler.parse(raw))
        assert not await store.insert(handler.parse(raw))
        await store.close()

    asyncio.run(exercise())


def test_recent_is_isolated_by_account_and_subject(tmp_path: Path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        for obs in (observation(1), observation(2, group_id=9), observation(3, self_id=20000)):
            await store.insert(obs)
        rows = await store.recent("qq", "10000", "qq:group:42")
        assert [row.source.source_message_id for row in rows] == ["1"]
        await store.close()

    asyncio.run(exercise())
