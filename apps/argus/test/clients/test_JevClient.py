import asyncio

from apps.argus.src.clients.JevClient import JevClient


QUESTIONS = {"should_wake": object()}


def client(**kwargs):
    return JevClient(answer=kwargs.pop("answer"), **kwargs)


def test_decide_returns_named_question_outcome():
    calls = []

    async def answer(state, questions):
        calls.append((state, questions))
        return {"should_wake": "wake", "another_question": "whatever"}

    async def exercise():
        judgement = await client(answer=answer).decide(
            state={"content": "你好"}, questions=QUESTIONS,
            question="should_wake", outcomes=("wake", "silence"), fallback="silence",
        )
        assert judgement.outcome == "wake"
        assert judgement.reason is None
        assert calls[0][0] == {"content": "你好"}
        assert calls[0][1] is QUESTIONS

    asyncio.run(exercise())


def test_decide_retries_three_times_then_falls_back():
    attempts = []
    delays = []

    async def answer(state, questions):
        attempts.append(True)
        raise ConnectionError("offline")

    async def sleep(seconds):
        delays.append(seconds)

    async def exercise():
        judgement = await client(answer=answer, sleep=sleep).decide(
            state={}, questions=QUESTIONS,
            question="should_wake", outcomes=("wake", "silence"), fallback="silence",
        )
        assert judgement.outcome == "silence"
        assert judgement.reason == "judge_fallback"
        assert len(attempts) == 3 and len(delays) == 2

    asyncio.run(exercise())


def test_decide_rejects_outcome_outside_allowed_set():
    attempts = []

    async def answer(state, questions):
        attempts.append(True)
        return {"should_wake": "maybe"}

    async def exercise():
        judgement = await client(
            answer=answer, sleep=lambda _: asyncio.sleep(0)
        ).decide(
            state={}, questions=QUESTIONS,
            question="should_wake", outcomes=("wake", "silence"), fallback="silence",
        )
        assert judgement.outcome == "silence" and judgement.reason == "judge_fallback"
        assert len(attempts) == 3

    asyncio.run(exercise())


def test_decide_missing_question_answer_falls_back():
    async def answer(state, questions):
        return {"another_question": "wake"}

    async def exercise():
        judgement = await client(
            answer=answer, sleep=lambda _: asyncio.sleep(0)
        ).decide(
            state={}, questions=QUESTIONS,
            question="should_wake", outcomes=("wake", "silence"), fallback="silence",
        )
        assert judgement.outcome == "silence" and judgement.reason == "judge_fallback"

    asyncio.run(exercise())


def test_fallback_must_be_an_accepted_outcome():
    async def exercise():
        try:
            await client(answer=lambda *_: None).decide(
                state={}, questions=QUESTIONS, question="should_wake",
                outcomes=("wake", "silence"), fallback="unknown",
            )
        except ValueError:
            return
        raise AssertionError("fallback outside outcomes must be rejected")

    asyncio.run(exercise())


def test_real_client_requires_api_key():
    try:
        JevClient(base_url="https://openrouter.ai/api", model="typesafe/jev-1.13")
    except ValueError:
        return
    raise AssertionError("api_key is required without an injected answer callable")
