import asyncio
import sys
import types

from apps.agent.src.model_config import EmbeddingSettings
from apps.agent.src.model_provider.impl.fastembed_embedding import (
    FastEmbedEmbedding,
)


class Vector:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class FakeTextEmbedding:
    instances = []

    def __init__(self, *, model_name, cache_dir):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.calls = []
        self.__class__.instances.append(self)

    def query_embed(self, text):
        self.calls.append(("query", text))
        return [Vector([0, 0.5])]

    def passage_embed(self, texts):
        self.calls.append(("passage", texts))
        return [Vector([index, index + 0.5]) for index, _ in enumerate(texts)]


def make_embedding(monkeypatch, tmp_path):
    FakeTextEmbedding.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "fastembed",
        types.SimpleNamespace(TextEmbedding=FakeTextEmbedding),
    )
    settings = EmbeddingSettings(
        provider="fastembed",
        model_name=(
            "sentence-transformers/"
            "paraphrase-multilingual-MiniLM-L12-v2"
        ),
    )
    return FastEmbedEmbedding(settings=settings, cache_dir=tmp_path)


def test_init_不导入或初始化模型(monkeypatch, tmp_path):
    embedding = make_embedding(monkeypatch, tmp_path)

    assert embedding._model is None
    assert FakeTextEmbedding.instances == []


def test_embed_query_在线程中调用包提供的query接口并返回普通列表(
    monkeypatch, tmp_path
):
    embedding = make_embedding(monkeypatch, tmp_path)
    calls = []

    async def fake_to_thread(function):
        calls.append(function)
        return function()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    vector = asyncio.run(embedding.embed_query("hello"))

    assert vector == [0.0, 0.5]
    assert embedding._model.model_name == (
        "sentence-transformers/"
        "paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert embedding._model.cache_dir == str(tmp_path)
    assert embedding._model.calls == [("query", "hello")]
    assert len(calls) == 2


def test_embed_documents_单次批量调用包提供的passage接口并保持顺序(
    monkeypatch, tmp_path
):
    embedding = make_embedding(monkeypatch, tmp_path)

    vectors = asyncio.run(embedding.embed_documents(["first", "second"]))

    assert vectors == [[0.0, 0.5], [1.0, 1.5]]
    assert embedding._model.calls == [("passage", ["first", "second"])]


def test_embed_documents_空列表不调用模型(monkeypatch, tmp_path):
    embedding = make_embedding(monkeypatch, tmp_path)

    vectors = asyncio.run(embedding.embed_documents([]))

    assert vectors == []
    assert embedding._model is None
    assert FakeTextEmbedding.instances == []


def test_embed_query_并发首次调用只初始化一次(monkeypatch, tmp_path):
    embedding = make_embedding(monkeypatch, tmp_path)

    async def run_concurrently():
        return await asyncio.gather(
            embedding.embed_query("first"),
            embedding.embed_query("second"),
        )

    vectors = asyncio.run(run_concurrently())

    assert vectors == [[0.0, 0.5], [0.0, 0.5]]
    assert len(FakeTextEmbedding.instances) == 1


def test_model初始化调用方取消后后台结果仍被回收(monkeypatch, tmp_path):
    embedding = make_embedding(monkeypatch, tmp_path)
    release = asyncio.Event()

    def create_model():
        return FakeTextEmbedding(
            model_name=embedding._settings.model_name,
            cache_dir=str(tmp_path),
        )

    async def delayed_to_thread(function):
        if function == embedding._create_model:
            await release.wait()
        return function()

    monkeypatch.setattr(embedding, "_create_model", create_model)
    monkeypatch.setattr(asyncio, "to_thread", delayed_to_thread)

    async def run():
        caller = asyncio.create_task(embedding._get_model())
        await asyncio.sleep(0)
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert embedding._model is not None
    assert embedding._model_task is None


def test_aclose对仍在运行的模型任务有界返回(monkeypatch, tmp_path):
    embedding = make_embedding(monkeypatch, tmp_path)
    never_finishes = asyncio.Event()

    async def run():
        embedding._model_task = asyncio.create_task(never_finishes.wait())

        async def immediate_timeout(awaitable, timeout):
            awaitable.cancel()
            raise TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)
        await embedding.aclose()
        embedding._model_task.cancel()
        await asyncio.gather(embedding._model_task, return_exceptions=True)

    asyncio.run(run())


def test_aclose不吞掉外部取消(monkeypatch, tmp_path):
    embedding = make_embedding(monkeypatch, tmp_path)
    never_finishes = asyncio.Event()

    async def run():
        embedding._model_task = asyncio.create_task(never_finishes.wait())

        async def cancel_from_outer_timeout(awaitable, timeout):
            awaitable.cancel()
            raise asyncio.CancelledError

        monkeypatch.setattr(
            asyncio,
            "wait_for",
            cancel_from_outer_timeout,
        )
        try:
            await embedding.aclose()
        except asyncio.CancelledError:
            propagated = True
        else:
            propagated = False
        embedding._model_task.cancel()
        await asyncio.gather(embedding._model_task, return_exceptions=True)
        return propagated

    assert asyncio.run(run()) is True
