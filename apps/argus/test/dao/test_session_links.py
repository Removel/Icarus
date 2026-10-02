import asyncio

from apps.argus.test.persistence_fakes import ObservationStore, SessionLink
from apps.argus.src.handler.qq_handler import QQHandler


def test_session_link_replacement_keeps_only_one_current(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        first = SessionLink("qq", "10000", "qq:group:42", "/work", "key-1", "session-1")
        second = SessionLink("qq", "10000", "qq:group:42", "/work", "key-2", "session-2")
        await store.save_link(first)
        assert await store.current_link("qq", "10000", "qq:group:42") == first
        await store.save_link(second)
        assert await store.current_link("qq", "10000", "qq:group:42") == second
        await store.close()

    asyncio.run(exercise())
