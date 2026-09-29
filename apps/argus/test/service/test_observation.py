import asyncio
import time

from apps.argus.src.clients.JevClient import Judgement
from apps.argus.src.dao.observation_store import ObservationStore, SessionLink
from apps.argus.src.handler.QQHandler import QQHandler
from apps.argus.src.service.QQService import QQService


class CapturingJudge:
    """Records the state handed to Jev and always answers silence."""

    def __init__(self, outcome="silence"):
        self.states = []
        self.questions = []
        self.outcome = outcome

    async def decide(self, *, state, questions, question, outcomes, fallback):
        self.states.append(state)
        self.questions.append((question, tuple(outcomes), fallback))
        return Judgement(self.outcome)


def group_message(message_id, text, group_id=42):
    return QQHandler("10000").parse({
        "post_type": "message", "self_id": 10000, "time": int(time.time()),
        "message_id": message_id, "message_type": "group", "group_id": group_id,
        "user_id": 7, "message": text,
    })


def test_self_message_is_silenced_without_calling_jev(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = QQHandler("10000").parse({
            "post_type": "message_sent", "self_id": 10000, "time": int(time.time()),
            "message_id": 321, "message_type": "private", "user_id": 10000,
            "target_id": 7, "message": "回复",
        })
        await store.insert(obs)
        judge = CapturingJudge()
        await QQService(store, judge, None, workspace_path=str(tmp_path)).process(obs)
        assert judge.states == []
        assert await store.status(obs.id) == "finished"
        await store.close()

    asyncio.run(exercise())


def test_existing_session_is_visible_to_judge(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = group_message(323, "继续")
        await store.insert(obs)
        await store.save_link(
            SessionLink("qq", "10000", obs.subject.key, str(tmp_path), "key", "session")
        )
        judge = CapturingJudge()
        await QQService(store, judge, None, workspace_path=str(tmp_path)).process(obs)
        assert judge.states[0]["current_session"] is True
        await store.close()

    asyncio.run(exercise())


def test_state_carries_qq_semantics_and_recent_messages(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        first = group_message(410, "上一句")
        await store.insert(first)
        second = group_message(411, "icarus 在吗")
        await store.insert(second)
        judge = CapturingJudge()
        await QQService(store, judge, None, workspace_path=str(tmp_path),
                        bot_names=("icarus", "伊卡洛斯")).process(second)
        state = judge.states[0]
        assert state["content"] == "icarus 在吗"
        assert state["chat_type"] == "group" and state["is_direct"] is False
        assert state["mentioned"] is False and state["name_mentioned"] is True
        assert state["bot_names"] == ["icarus", "伊卡洛斯"]
        assert [item for item in state["recent_messages"][:2]] == ["icarus 在吗", "上一句"]
        assert judge.questions[0][0] == "should_wake"
        assert judge.questions[0][1] == ("wake", "silence")
        await store.close()

    asyncio.run(exercise())


def test_bot_name_matching_ignores_case_width_and_image_references(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        judge = CapturingJudge()
        service = QQService(store, judge, None, workspace_path=str(tmp_path),
                            bot_names=("icarus", "伊卡洛斯"))
        cases = ((401, "ＩＣＡＲＵＳ 在吗", True), (402, "伊卡洛斯帮个忙", True),
                 (403, "今天很热", False))
        for message_id, text, expected in cases:
            obs = group_message(message_id, text)
            await store.insert(obs)
            await service.process(obs)
        image_only = QQHandler("10000").parse({
            "post_type": "message", "self_id": 10000, "time": int(time.time()),
            "message_id": 404, "message_type": "group", "group_id": 42, "user_id": 7,
            "message": [{"type": "image", "data": {"file": "icarus.png"}}],
        })
        await store.insert(image_only)
        await service.process(image_only)
        assert [state["name_mentioned"] for state in judge.states] == [True, True, False, False]
        await store.close()

    asyncio.run(exercise())


def test_fallback_reason_is_recorded_distinctly(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = group_message(322, "闲聊")
        await store.insert(obs)

        class FallbackJudge:
            async def decide(self, *, state, questions, question, outcomes, fallback):
                return Judgement(fallback, "judge_fallback")

        await QQService(store, FallbackJudge(), None, workspace_path=str(tmp_path)).process(obs)
        details = await store.status_details(obs.id)
        assert details["status"] == "finished"
        assert details["judgment"] == "silence" and details["reason"] == "judge_fallback"
        await store.close()

    asyncio.run(exercise())


def test_expired_observation_is_silenced(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        old = QQHandler("10000").parse({
            "post_type": "message", "self_id": 10000, "time": int(time.time()) - 3600,
            "message_id": 430, "message_type": "group", "group_id": 42,
            "user_id": 7, "message": "icarus 在吗",
        })
        await store.insert(old)
        judge = CapturingJudge()
        await QQService(store, judge, None, workspace_path=str(tmp_path)).process(old)
        assert judge.states == []
        assert (await store.status_details(old.id))["reason"] == "expired"
        await store.close()

    asyncio.run(exercise())
