import asyncio
import json
import logging

import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.judge import JevJudge, JudgeUnavailable, NullJudge, create_judge


def response_for(questions, score=1.6, probability=0.9):
    answers = {}
    for key, question in questions.items():
        if question["type"] == "score":
            answers[key] = {"type": "score", "score": score, "confidence": 0.9,
                            "legend": {"0": "safe", "1": "maybe", "2": "clear"},
                            "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}}
        else:
            answers[key] = {"type": "noul", "noul": probability}
    return {"model": "typesafe/jev-1.13", "usage": {"input_tokens": 30, "output_tokens": 3}, "answers": answers}


def build_judge(handler, timeout=0.2):
    client = AsyncTypeSafeClient(api_key="test-key", base_url="https://judge.example/api",
        transport=httpx2.MockTransport(handler), retry=RetryPolicy(max_retries=0))
    return JevJudge(client, timeout_seconds=timeout)


def test_attack_uses_three_level_score_and_normalizes():
    async def run():
        def handler(request):
            data = json.loads(request.content)
            assert request.url.path == "/api/v1/systemone"
            assert data["state"]["input"] == "hello"
            assert len(data["questions"]["attack"]["criteria"]) == 3
            return httpx2.Response(200, json=response_for(data["questions"]))
        judge = build_judge(handler)
        try:
            assert await judge.attack_confidence("hello") == 0.8
        finally:
            await judge.aclose()
    asyncio.run(run())


def test_drift_and_evolution_send_bounded_typed_questions():
    async def run():
        requests = []
        def handler(request):
            data = json.loads(request.content)
            requests.append(data)
            return httpx2.Response(200, json=response_for(data["questions"]))
        judge = build_judge(handler)
        try:
            await judge.drift_confidence([str(n) * 3000 for n in range(9)], "warm")
            verdict = await judge.evolution_verdict(note="I am warmer", reason="preference",
                section="voice", recent=[("user", "X" * 2000)] * 20, invariants="core")
            assert verdict.grounded == 0.9
            assert set(requests[1]["questions"]) == {"grounded", "in_scope", "worth_persisting"}
            assert all(q["type"] == "noul" for q in requests[1]["questions"].values())
            assert len(requests[0]["state"]["assistant"]) <= 5
            assert sum(map(len, requests[0]["state"]["assistant"])) <= 4000
            recent = requests[1]["state"]["recent"]
            assert len(recent) <= 8
            assert sum(len(row[1]) for row in recent) <= 4000
        finally:
            await judge.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["timeout", "http", "malformed", "nan", "wrong_type"])
def test_failed_calls_raise_safe_judge_unavailable_without_retries(mode):
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            if mode == "timeout":
                await asyncio.Event().wait()
            if mode == "http":
                return httpx2.Response(503, text="SECRET-ERROR")
            data = json.loads(request.content)
            result = response_for(data["questions"])
            if mode == "malformed":
                result["answers"] = {}
            elif mode == "nan":
                result["answers"]["attack"]["score"] = float("nan")
            elif mode == "wrong_type":
                result["answers"]["attack"] = {"type": "noul", "noul": 0.9}
            if mode == "nan":
                return httpx2.Response(200, content=json.dumps(result))
            return httpx2.Response(200, json=result)
        judge = build_judge(handler, timeout=0.03)
        try:
            with pytest.raises(JudgeUnavailable) as error:
                await judge.attack_confidence("PRIVATE-INPUT")
            assert "PRIVATE" not in str(error.value) and "SECRET" not in str(error.value)
            assert len(calls) == 1
        finally:
            await judge.aclose()
    asyncio.run(run())


def test_key_precedence_and_missing_key_degrades(monkeypatch, caplog):
    monkeypatch.delenv("ICARUS_JEV_API_KEY", raising=False)
    monkeypatch.delenv("ARGUS_JEV_API_KEY", raising=False)
    assert isinstance(create_judge(PersonaConfig(jev_enabled=True)), NullJudge)
    assert caplog.records
    seen = []
    def factory(**kwargs):
        seen.append(kwargs)
        return object()
    monkeypatch.setattr("agent_app.plugins.persona.judge.AsyncTypeSafeClient", factory)
    monkeypatch.setenv("ARGUS_JEV_API_KEY", "argus-test")
    create_judge(PersonaConfig(jev_enabled=True))
    monkeypatch.setenv("ICARUS_JEV_API_KEY", "icarus-test")
    create_judge(PersonaConfig(jev_enabled=True))
    assert [options["api_key"] for options in seen] == ["argus-test", "icarus-test"]
    assert all(options["retry"].max_retries == 0 for options in seen)


def test_no_secret_or_prompt_text_in_debug_logs(caplog):
    async def run():
        def handler(request):
            data = json.loads(request.content)
            return httpx2.Response(200, json=response_for(data["questions"]))
        judge = build_judge(handler)
        try:
            with caplog.at_level(logging.DEBUG):
                await judge.attack_confidence("PRIVATE-PROMPT")
            assert "PRIVATE-PROMPT" not in caplog.text
            assert "test-key" not in caplog.text
        finally:
            await judge.aclose()
    asyncio.run(run())
