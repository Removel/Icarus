import asyncio

from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.handler.QQHandler import QQHandler
from apps.argus.src.service.QQService import QQService
from apps.argus.src.clients.IcarusClient import GatewayTransportError, GatewayBusinessError


def make_obs(message_id=300):
    return QQHandler("10000").parse({
        "post_type": "message", "self_id": 10000, "time": 1700000000,
        "message_id": message_id, "message_type": "group", "group_id": 42,
        "user_id": 7, "message": "帮我查一下",
    })


class FakeGateway:
    def __init__(self):
        self.sessions = {}
        self.calls = []
        self.fail_submit = False

    async def session_get(self, path, session_id):
        self.calls.append(("get", session_id))
        if session_id not in self.sessions:
            raise GatewayBusinessError("session_not_found", "Session is not found")
        return self.sessions[session_id]

    async def session_create(self, path, session_id):
        self.calls.append(("create", session_id))
        self.sessions[session_id] = {"session_id": session_id, "workspace_key": "server-key", "lifecycle": "unloaded"}
        return self.sessions[session_id]

    async def session_submit(self, path, session_id, prompt, submission_id):
        self.calls.append(("submit", session_id, submission_id, prompt))
        if self.fail_submit:
            raise GatewayTransportError("lost response")
        return {"task_id": "task-1", "queue_position": 0}


def test_new_session_then_reuses_unloaded_link(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        dispatcher = QQService(store, None, gateway, workspace_path=str(tmp_path), reply_hint=lambda _: " 回复目标")
        first = make_obs()
        await store.insert(first)
        assert await store.claim(first.id)
        assert await dispatcher.dispatch(first, "qq-wake-v1") == "task-1"
        link = await store.current_link("qq", "10000", "qq:group:42")
        assert link.workspace_key == "server-key"
        second = make_obs(301)
        await store.insert(second)
        assert await store.claim(second.id)
        assert await dispatcher.dispatch(second, "qq-wake-v1") == "task-1"
        assert len([c for c in gateway.calls if c[0] == "create"]) == 1
        assert len([c for c in gateway.calls if c[0] == "submit"]) == 2
        assert all("回复目标" in c[3] for c in gateway.calls if c[0] == "submit")
        await store.close()

    asyncio.run(exercise())


def test_missing_recorded_session_creates_new_one(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        service = QQService(store, None, gateway, workspace_path=str(tmp_path))
        obs = make_obs()
        await store.insert(obs)
        await store.claim(obs.id)
        from apps.argus.src.dao.observation_store import SessionLink
        await store.save_link(SessionLink("qq", "10000", "qq:group:42", str(tmp_path), "old-key", "missing"))
        assert await service.dispatch(obs, "qq-wake-v1") == "task-1"
        assert len([c for c in gateway.calls if c[0] == "create"]) == 1
        await store.close()
    asyncio.run(exercise())


def test_link_from_other_workspace_is_not_used(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        service = QQService(store, None, gateway, workspace_path=str(tmp_path))
        obs = make_obs()
        await store.insert(obs)
        await store.claim(obs.id)
        from apps.argus.src.dao.observation_store import SessionLink
        await store.save_link(SessionLink("qq", "10000", "qq:group:42", "/other", "server-key", "old"))
        gateway.sessions["old"] = {"session_id": "old", "workspace_key": "server-key", "lifecycle": "ready"}
        await service.dispatch(obs, "qq-wake-v1")
        assert all(c[1] != "old" for c in gateway.calls if c[0] == "submit")
        await store.close()
    asyncio.run(exercise())


def test_session_create_response_lost_does_not_create_another_session(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        async def lost_response(path, session_id):
            await FakeGateway.session_create(gateway, path, session_id)
            raise GatewayTransportError("response lost")
        gateway.session_create = lost_response
        service = QQService(store, None, gateway, workspace_path=str(tmp_path))
        obs = make_obs()
        await store.insert(obs)
        await store.claim(obs.id)
        assert await service.dispatch(obs, "qq-wake-v1") == "task-1"
        assert len([call for call in gateway.calls if call[0] == "create"]) == 1
        await store.close()
    asyncio.run(exercise())


def test_mismatched_gateway_workspace_does_not_submit_or_kill_worker(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        service = QQService(store, None, gateway, workspace_path=str(tmp_path))
        obs = make_obs()
        await store.insert(obs)
        await store.claim(obs.id)
        from apps.argus.src.dao.observation_store import SessionLink
        await store.save_link(SessionLink("qq", "10000", obs.subject.key, str(tmp_path), "old-key", "existing"))
        gateway.sessions["existing"] = {"session_id": "existing", "workspace_key": "wrong-key", "lifecycle": "ready"}
        await service.dispatch(obs, "qq-wake-v1")
        assert await store.status(obs.id) == "failed"
        assert not any(c[0] == "submit" for c in gateway.calls)
        await store.close()
    asyncio.run(exercise())


def test_unknown_gateway_business_error_does_not_claim_rejection(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        async def internal_error(path, session_id, prompt, submission_id):
            raise GatewayBusinessError("-32603", "Internal error")
        gateway.session_submit = internal_error
        service = QQService(store, None, gateway, workspace_path=str(tmp_path))
        obs = make_obs()
        await store.insert(obs)
        await store.claim(obs.id)
        await service.dispatch(obs, "qq-wake-v1")
        assert await store.status(obs.id) == "submission_unknown"
        await store.close()
    asyncio.run(exercise())


def test_unavailable_gateway_before_submit_releases_observation_for_recovery(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        async def unavailable(path, session_id):
            raise GatewayTransportError("Gateway offline")
        gateway.session_get = unavailable
        service = QQService(store, None, gateway, workspace_path=str(tmp_path))
        obs = make_obs()
        await store.insert(obs)
        await store.claim(obs.id)
        from apps.argus.src.dao.observation_store import SessionLink
        await store.save_link(SessionLink("qq", "10000", obs.subject.key, str(tmp_path), "server-key", "old"))
        await service.dispatch(obs, "qq-wake-v1")
        assert await store.status(obs.id) == "pending"
        assert await store.recover() == (obs,)
        await store.close()
    asyncio.run(exercise())


def test_lost_submit_response_stays_unknown_and_is_not_resent(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        gateway = FakeGateway()
        gateway.fail_submit = True
        dispatcher = QQService(store, None, gateway, workspace_path=str(tmp_path), reply_hint=lambda _: "")
        obs = make_obs()
        await store.insert(obs)
        await store.claim(obs.id)
        await dispatcher.dispatch(obs, "qq-wake-v1")
        assert await store.status(obs.id) == "submission_unknown"
        await dispatcher.dispatch(obs, "qq-wake-v1")
        assert len([call for call in gateway.calls if call[0] == "submit"]) == 1
        await store.close()

    asyncio.run(exercise())
