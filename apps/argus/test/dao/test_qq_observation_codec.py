import asyncio
from pathlib import Path

import pytest
from sqlalchemy import update

from apps.argus.src.dao.qq_observation_codec import UnsupportedObservationError
from apps.argus.src.dao.entities import _ObservationRow
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.handler.qq_handler import QQHandler


def test_unknown_detail_version_is_not_silently_downgraded(tmp_path: Path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = QQHandler("10000").parse({
            "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": 9,
            "message_type": "private", "user_id": 7, "message": "你好",
        })
        await store.insert(obs)
        async with store._require_sessions().begin() as db:
            await db.execute(update(_ObservationRow).where(_ObservationRow.id == obs.id).values(detail_version=2))
        with pytest.raises(UnsupportedObservationError):
            await store.get(obs.id)
        await store.close()

    asyncio.run(exercise())
