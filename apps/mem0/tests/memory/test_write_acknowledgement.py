"""A failed vector insert must never be reported or audited as an ADD."""

import asyncio
from unittest.mock import MagicMock

import pytest

from mem0.memory.main import AsyncMemory, Memory


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fallback_succeeds", [False, True])
def test_add_acknowledges_only_persisted_memories(monkeypatch, asynchronous, fallback_succeeds):
    kind = AsyncMemory if asynchronous else Memory
    memory = kind.__new__(kind)
    memory.custom_instructions = None
    memory.api_version = "v1.1"
    memory.db = MagicMock()
    memory.db.get_last_messages.return_value = []
    memory.embedding_model = MagicMock()
    memory.embedding_model.embed.return_value = [0.1, 0.2, 0.3]
    memory.embedding_model.embed_batch.return_value = [[0.1, 0.2, 0.3]]
    memory.llm = MagicMock()
    memory.llm.generate_response.return_value = '{"memory": [{"text": "需要保存的事实"}]}'
    memory.vector_store = MagicMock()
    memory.vector_store.search.return_value = []
    failure = RuntimeError("isolated vector store failure")
    memory.vector_store.insert.side_effect = [failure, None] if fallback_succeeds else failure
    monkeypatch.setattr("mem0.memory.main.capture_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    monkeypatch.setattr("mem0.memory.main.extract_entities_batch", lambda texts: [[] for text in texts])

    def add():
        result = memory._add_to_vector_store(
            [dict(role="user", content="需要保存的事实")], {}, {"user_id": "test-user"}, True,
        )
        return asyncio.run(result) if asynchronous else result

    if fallback_succeeds:
        result = add()
        assert len(result) == 1 and result[0]["memory"] == "需要保存的事实"
        assert memory.db.batch_add_history.call_args.args[0][0]["memory_id"] == result[0]["id"]
    else:
        with pytest.raises(RuntimeError, match="persistence failed"):
            add()
        memory.db.batch_add_history.assert_not_called()
