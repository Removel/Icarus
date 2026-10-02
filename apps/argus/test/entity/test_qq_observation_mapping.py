import asyncio
from pathlib import Path

import pytest
from sqlalchemy import update

from apps.argus.src.entity.qq_observation import UnsupportedObservationError
from apps.argus.src.entity.records import ObservationRow
from apps.argus.test.persistence_fakes import ObservationStore
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
        async with store.database.begin(immediate=True) as db:
            await db.execute(update(ObservationRow).where(ObservationRow.id == obs.id).values(detail_version=2))
        with pytest.raises(UnsupportedObservationError):
            await store.get(obs.id)
        await store.close()

    asyncio.run(exercise())
