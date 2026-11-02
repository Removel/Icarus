"""Real PostgreSQL contract tests. Set ARGUS_TEST_DATABASE_URL to a disposable DB."""
import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from argus_app.contracts.models import Decision, DomainError
from argus_app.infrastructure.store import Store


def validate_test_database_url(url):
    from sqlalchemy.engine import make_url
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or not (parsed.database or "").endswith("_test"):
        raise ValueError("Store tests require a disposable PostgreSQL database ending _test")
    return url


@pytest_asyncio.fixture
async def store():
    url = os.environ.get("ARGUS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ARGUS_TEST_DATABASE_URL must point to a disposable PostgreSQL database")
    instance = Store(validate_test_database_url(url))
    await instance.start()
    async with instance.engine.begin() as conn:
        await conn.execute(text("TRUNCATE observed_items, channels RESTART IDENTITY"))
    yield instance
    await instance.close()


@pytest.fixture
def clock(monkeypatch):
    from datetime import datetime, timedelta, timezone
    class Clock(datetime):
        value = datetime(2026, 11, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.value

        @classmethod
        def advance(cls):
            cls.value += timedelta(hours=1)

    monkeypatch.setattr("argus_app.infrastructure.store.datetime", Clock)
    return Clock


async def message(store, mid="1", text="hello", source="qq:1"):
    return await store.ingest(source, mid, "message.observed", {"text": text})


async def channel(store, sources=None):
    return await store.create_channel("test", "goal", sources or ["qq:1"])


async def passed_round(store, count=1):
    ch = await channel(store)
    items = []
    for i in range(count):
        item = await message(store, str(i))
        items.append(item)
        ch = await store.channel(ch.id)
        assert await store.decide(ch.id, item.id, ch.version, {"passed": True, "reason": "match"})
    return ch, items, await store.begin_round(ch.id)


async def control(store, round_, kind, payload, rid=None):
    import uuid
    item = await store.ingest("agent", rid or str(uuid.uuid4()), kind, payload,
                              {"binding_id": round_["binding_id"], "payload": payload})
    return item, await store.apply_control(item.id)


@pytest.mark.asyncio
async def test_latest_revision_dedupe_and_history(store):
    a = await message(store, text="A")
    assert (await message(store, text="A")).id == a.id
    b = await message(store, text="B")
    a2 = await message(store, text="A")
    assert [a.revision, b.revision, a2.revision] == [1, 2, 3]
    assert a2.id != a.id
    assert [x.id for x in await store.observations(["qq:1"])] == [a2.id]
    assert (await store.observation(b.id)).content["text"] == "B"
    assert await store.observations(["qq:1"], text="missing") == []
    assert [x.id for x in await store.observations(["qq:1"], text="A")] == [a2.id]


@pytest.mark.asyncio
async def test_only_two_tables_and_restart_cursor(store):
    ch = await channel(store)
    item = await message(store)
    assert [x.id for x in await store.unread(ch.id)] == [item.id]
    assert not await store.decide(ch.id, item.id, 999, {"passed": False, "reason": "ignore"})
    assert await store.decide(ch.id, item.id, ch.version, {"passed": False, "reason": "ignore"})
    other = Store(os.environ["ARGUS_TEST_DATABASE_URL"])
    await other.start()
    try:
        assert await other.unread(ch.id) == []
        assert (await other.channel(ch.id)).runtime.get("pending", {}) == {}
        async with other.engine.connect() as conn:
            tables = (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))).scalars().all()
        assert set(tables) == {"observed_items", "channels"}
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_freezes_round_and_requires_delivered_all_confirmed_before_publish(store):
    ch, items, round_ = await passed_round(store, 2)
    assert round_ == await store.begin_round(ch.id)
    await store.set_round_delivery(ch.id, round_["wake_id"], "session", "task")
    _, early = await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    assert not early["applied"]
    _, delivered = await control(store, round_, "agent.delivered", {"observation_ids": [x.id for x in items]})
    assert delivered["applied"]
    _, result = await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    assert result["applied"]
    current = await store.channel(ch.id)
    _, premature = await control(store, round_, "channel.update_requested", {"expected_version": current.version, "progress": "done"})
    assert not premature["applied"]
    assert premature["code"] == "events_not_confirmed"
    _, result = await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[1].id, "status": "no_action"}]})
    assert result["applied"]
    current = await store.channel(ch.id)
    update, result = await control(store, round_, "channel.update_requested", {"expected_version": current.version, "progress": "done"}, "update-1")
    assert result["applied"]
    assert await store.apply_control(update.id) == result
    assert (await store.channel(ch.id)).progress == "done"
    _, result = await control(store, round_, "agent.finish_requested", {"update": "committed"})
    assert result["applied"]
    assert await store.begin_round(ch.id) is None
    assert (await store.channel(ch.id)).runtime["pending"] == {}
    with pytest.raises(DomainError):
        await store.bound_channel(round_["binding_id"])


@pytest.mark.asyncio
async def test_atomic_reject_cross_channel_unfrozen_and_invalid_confirmation(store):
    ch, items, round_ = await passed_round(store)
    foreign = await message(store, "foreign", source="qq:other")
    _, result = await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id, foreign.id]})
    assert not result["applied"]
    assert (await store.begin_round(ch.id))["delivered"] == []
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    _, result = await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}, {"observation_id": foreign.id, "status": "handled"}]})
    assert not result["applied"]
    assert (await store.begin_round(ch.id))["confirmed"] == {}
    _, result = await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "bad"}]})
    assert not result["applied"]


@pytest.mark.asyncio
async def test_control_request_id_cannot_change_payload_binding_or_kind(store):
    ch, items, round_ = await passed_round(store)
    item, result = await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]}, "same")
    assert result["applied"]
    duplicate = await store.ingest("agent", "same", item.kind, item.content,
                                   {"binding_id": round_["binding_id"], "payload": item.content})
    assert duplicate.id == item.id
    for kind, payload, binding in [(item.kind, {}, round_["binding_id"]), ("agent.finish_requested", item.content, round_["binding_id"]), (item.kind, item.content, "other")]:
        with pytest.raises(DomainError) as exc:
            await store.ingest("agent", "same", kind, payload, {"binding_id": binding, "payload": payload})
        assert exc.value.code == "request_conflict"
    assert await store.apply_control(item.id) == result


@pytest.mark.asyncio
async def test_capacity_backpressure_errors_defer_and_no_cursor_advance(store):
    ch = await channel(store)
    # Full capacity through real public writes, never monkeypatch production state.
    for i in range(256):
        item = await message(store, str(i))
        ch = await store.channel(ch.id)
        assert await store.decide(ch.id, item.id, ch.version, {"passed": True, "reason": "yes"})
    overflow = await message(store, "overflow")
    ch = await store.channel(ch.id)
    with pytest.raises(DomainError) as exc:
        await store.decide(ch.id, overflow.id, ch.version, {"passed": True, "reason": "yes"})
    assert exc.value.code == "pending_capacity"
    assert [x.id for x in await store.unread(ch.id)] == [overflow.id]
    assert len((await store.channel(ch.id)).runtime["pending"]) == 256


@pytest.mark.asyncio
async def test_decision_cas_serialization_and_source_scope(store):
    ch = await channel(store)
    item = await message(store)
    results = await asyncio.gather(*[store.decide(ch.id, item.id, ch.version, {"passed": True, "reason": "match"}) for _ in range(2)])
    assert sorted(results) == [False, True]
    foreign = await message(store, "x", source="qq:other")
    current = await store.channel(ch.id)
    assert not await store.decide(ch.id, foreign.id, current.version, {"passed": True, "reason": "match"})


@pytest.mark.asyncio
async def test_fixed_check_results_pending_replay_and_finish_unchanged(store):
    ch, items, round_ = await passed_round(store)
    check = await store.ingest("agent", "check", "rule.check_requested", {"source": "x"}, {"binding_id": round_["binding_id"]})
    assert check.id in [x.id for x in await store.pending_controls()]
    assert await store.complete_control(check.id, {"applied": True, "valid": True}) == {"applied": True, "valid": True}
    assert await store.complete_control(check.id, {"applied": False, "valid": False}) == {"applied": True, "valid": True}
    assert check.id not in [x.id for x in await store.pending_controls()]
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "no_action"}]})
    _, result = await control(store, round_, "agent.finish_requested", {"update": "committed"})
    assert not result["applied"]
    _, result = await control(store, round_, "agent.finish_requested", {"update": "unchanged"})
    assert result["applied"]


@pytest.mark.asyncio
async def test_malformed_controls_are_fixed_rejections_not_server_errors(store):
    ch, items, round_ = await passed_round(store)
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    malformed, result = await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": []}]})
    assert not result["applied"]
    assert await store.apply_control(malformed.id) == result
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    ch = await store.channel(ch.id)
    _, result = await control(store, round_, "channel.update_requested", {"expected_version": ch.version, "lifecycle": {}})
    assert not result["applied"]
    assert (await store.channel(ch.id)).lifecycle == "active"


@pytest.mark.asyncio
async def test_history_bound_is_revision_snapshot_not_live_latest(store):
    first = await message(store, text="before")
    ch = await channel(store)
    assert await store.decide(ch.id, first.id, ch.version, {"passed": False, "reason": "ignore"})
    bounds = (await store.channel(ch.id)).subscriptions
    await message(store, text="after")
    snapshot = await store.observations(["qq:1"], bounds=bounds)
    assert [x.id for x in snapshot] == [first.id]
    assert await store.observations(["qq:other"], bounds=bounds) == []


@pytest.mark.asyncio
async def test_error_and_defer_retry_survive_restart_and_ignored_retry_removed(store, clock):
    ch = await channel(store)
    first = await message(store)
    assert await store.decide(ch.id, first.id, ch.version, {"error": "rule timed out"})
    ch = await store.channel(ch.id)
    assert ch.runtime["pending"][first.id]["status"] == "error"
    clock.advance()
    assert [x.id for x in await store.unread(ch.id)] == [first.id]
    assert await store.decide(ch.id, first.id, ch.version, {"passed": False, "defer": True, "reason": "wait"})
    ch = await store.channel(ch.id)
    assert ch.runtime["pending"][first.id]["status"] == "deferred"
    assert await store.begin_round(ch.id) is None
    clock.advance()
    assert await store.decide(ch.id, first.id, ch.version, {"passed": False, "reason": "ignore"})
    assert await store.unread(ch.id) == []
    assert (await store.channel(ch.id)).runtime["pending"] == {}


@pytest.mark.asyncio
async def test_frozen_round_survives_new_pending_restart_and_stale_finish(store):
    ch, items, round_ = await passed_round(store)
    newer = await message(store, "later")
    current = await store.channel(ch.id)
    assert await store.decide(ch.id, newer.id, current.version, {"passed": True, "reason": "yes"})
    another = Store(os.environ["ARGUS_TEST_DATABASE_URL"])
    await another.start()
    try:
        assert await another.begin_round(ch.id) == round_
        await control(another, round_, "agent.delivered", {"observation_ids": [items[0].id]})
        await control(another, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
        _, result = await control(another, round_, "agent.finish_requested", {"update": "unchanged"})
        assert result["applied"]
        next_round = await another.begin_round(ch.id)
        assert next_round["observation_ids"] == [newer.id]
        assert next_round["binding_id"] != round_["binding_id"]
        _, stale = await control(another, round_, "agent.delivered", {"observation_ids": [newer.id]})
        assert not stale["applied"]
        assert (await another.begin_round(ch.id))["delivered"] == []
    finally:
        await another.close()


@pytest.mark.asyncio
async def test_retry_backoff_exhaustion_retains_fact_without_hot_loop(store, clock):
    ch = await channel(store)
    item = await message(store)
    for attempt in range(1, 4):
        ch = await store.channel(ch.id)
        assert await store.decide(ch.id, item.id, ch.version, {"error": "broken rule"})
        current = await store.channel(ch.id)
        assert current.runtime["pending"][item.id]["attempt_count"] == attempt
        assert await store.unread(ch.id) == []
        clock.advance()
        expected = [item.id] if attempt < 3 else []
        assert [x.id for x in await store.unread(ch.id)] == expected
    ch = await store.channel(ch.id)
    assert item.id in ch.runtime["pending"]
    assert not await store.decide(ch.id, item.id, ch.version, {"error": "still broken"})


@pytest.mark.asyncio
async def test_remaining_passed_items_rejudge_when_round_changes_channel_context(store):
    ch, items, round_ = await passed_round(store)
    second = await message(store, "second")
    current = await store.channel(ch.id)
    assert await store.decide(ch.id, second.id, current.version, {"passed": True, "reason": "old rule"})
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    current = await store.channel(ch.id)
    _, update = await control(store, round_, "channel.update_requested", {"expected_version": current.version, "progress": "new context"})
    assert update["applied"]
    await control(store, round_, "agent.finish_requested", {"update": "committed"})
    assert await store.begin_round(ch.id) is None
    assert [x.id for x in await store.unread(ch.id)] == [second.id]
    current = await store.channel(ch.id)
    assert await store.decide(ch.id, second.id, current.version, {"passed": False, "reason": "no longer relevant"})
    assert (await store.channel(ch.id)).runtime["pending"] == {}


@pytest.mark.asyncio
async def test_external_validation_can_persist_only_rejected_update(store):
    ch, _, round_ = await passed_round(store)
    request = await store.ingest("agent", "bad-rule", "channel.update_requested",
                                 {"expected_version": ch.version, "rule_hash": "bad"},
                                 {"binding_id": round_["binding_id"]})
    rejection = {"applied": False, "code": "rule_hash_mismatch"}
    assert await store.complete_control(request.id, rejection) == rejection
    assert await store.apply_control(request.id) == rejection
    assert (await store.channel(ch.id)).rule_hash == ""
    with pytest.raises(DomainError):
        await store.complete_control(request.id, {"applied": True})


@pytest.mark.asyncio
async def test_operational_writes_preserve_config_version_and_frozen_version(store):
    ch, items, round_ = await passed_round(store)
    assert round_["channel_version"] == ch.version
    await store.set_round_delivery(ch.id, round_["wake_id"], "session", "task")
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    assert (await store.channel(ch.id)).version == ch.version
    _, result = await control(store, round_, "channel.update_requested", {"expected_version": ch.version, "progress": "updated"})
    assert result["applied"]
    assert (await store.channel(ch.id)).version == ch.version + 1
    assert (await store.begin_round(ch.id))["channel_version"] == ch.version
    _, finish = await control(store, round_, "agent.finish_requested", {"update": "committed"})
    assert finish["applied"]
    assert (await store.channel(ch.id)).version == ch.version + 1


@pytest.mark.asyncio
async def test_empty_snapshot_bounds_never_leak_live_items(store):
    await message(store)
    assert await store.observations(["qq:1"], bounds={}) == []
    assert await store.observations(["qq:1"], bounds={"qq:1": None}) == []


@pytest.mark.asyncio
async def test_source_ingestion_commit_order_cannot_skip_slow_earlier_fact(store):
    async with store.engine.begin() as conn:
        await conn.execute(text("""CREATE FUNCTION test_delay_ingest() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.message_id = 'slow' THEN PERFORM pg_sleep(0.5); END IF;
            RETURN NEW;
        END $$"""))
        await conn.execute(text("CREATE TRIGGER test_delay BEFORE INSERT ON observed_items FOR EACH ROW EXECUTE FUNCTION test_delay_ingest()"))
    slow = asyncio.create_task(message(store, "slow", "slow"))
    try:
        async with asyncio.timeout(5):
            while True:
                async with store.engine.connect() as conn:
                    sleeping = (await conn.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event = 'PgSleep'"))).scalar_one()
                if sleeping:
                    break
                await asyncio.sleep(0.01)
        await message(store, "fast", "fast")
        ch = await channel(store)
        assert [item.content["text"] for item in await store.unread(ch.id)] == ["slow", "fast"]
    finally:
        await slow
        async with store.engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER test_delay ON observed_items"))
            await conn.execute(text("DROP FUNCTION test_delay_ingest()"))


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["paused", "completed"])
async def test_lifecycle_finalizes_round_and_preserves_or_clears_other_pending(store, lifecycle):
    ch, items, round_ = await passed_round(store)
    second = await message(store, "next")
    assert await store.decide(ch.id, second.id, ch.version, {"passed": True, "reason": "next"})
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    _, result = await control(store, round_, "channel.update_requested", {"expected_version": ch.version, "lifecycle": lifecycle})
    assert result["applied"]
    await control(store, round_, "agent.finish_requested", {"update": "committed"})
    ch = await store.channel(ch.id)
    assert ch.runtime["round"] is None
    assert await store.begin_round(ch.id) is None
    assert await store.unread(ch.id) == []
    if lifecycle == "paused":
        assert list(ch.runtime["pending"]) == [second.id]
        assert list(ch.subscriptions) == ["qq:1"]
    else:
        assert ch.runtime["pending"] == {}
        assert ch.subscriptions == {}


@pytest.mark.asyncio
async def test_history_literal_text_and_identifiers_cannot_change_sql(store):
    source = "qq:'); DROP TABLE channels; --"
    item = await message(store, "' OR true; --", "100%_literal", source)
    assert [x.id for x in await store.observations([source], text="%_")] == [item.id]
    assert await store.observations([source], text="' OR true; --") == []
    assert await store.channels() == []


@pytest.mark.asyncio
async def test_raw_hash_update_derives_path_without_breaking_closed_control_replay(store):
    ch, items, round_ = await passed_round(store)
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    payload = {"expected_version": ch.version, "rule_hash": "a" * 64}
    request, result = await control(store, round_, "channel.update_requested", payload, "raw-rule")
    assert result["applied"]
    assert (await store.channel(ch.id)).rule_path == f"{ch.id}/{'a' * 64}.py"
    finish, finished = await control(store, round_, "agent.finish_requested", {"update": "committed"}, "closed-finish")
    assert finished["applied"]
    duplicate = await store.ingest("agent", "raw-rule", "channel.update_requested", payload,
                                   {"binding_id": round_["binding_id"], "payload": payload})
    assert duplicate.id == request.id
    assert await store.apply_control(duplicate.id) == result
    assert await store.apply_control(finish.id) == finished


@pytest.mark.asyncio
async def test_source_replacement_reclaims_full_pending_capacity_but_keeps_frozen(store):
    ch = await channel(store)
    ids = []
    for i in range(256):
        item = await message(store, str(i))
        ids.append(item.id)
        assert await store.decide(ch.id, item.id, ch.version, {"passed": True, "reason": "A"})
    round_ = await store.begin_round(ch.id, limit=1)
    frozen = round_["observation_ids"]
    await control(store, round_, "agent.delivered", {"observation_ids": frozen})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": frozen[0], "status": "handled"}]})
    _, result = await control(store, round_, "channel.update_requested", {"expected_version": ch.version, "source_keys": ["qq:B"]})
    assert result["applied"]
    current = await store.channel(ch.id)
    assert set(current.runtime["pending"]) == set(frozen)
    assert (await store.begin_round(ch.id))["observation_ids"] == frozen
    await control(store, round_, "agent.finish_requested", {"update": "committed"})
    assert (await store.channel(ch.id)).runtime["pending"] == {}
    for i in range(256):
        item = await message(store, str(i), source="qq:B")
        assert await store.decide(ch.id, item.id, current.version, {"passed": True, "reason": "B"})
    assert len((await store.channel(ch.id)).runtime["pending"]) == 256


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [{"passed": False, "defer": True, "reason": "wait"}, {"error": "failed"}])
async def test_superseded_retry_is_retired_before_rejudgment(store, clock, decision):
    ch = await channel(store)
    first = await message(store, text="old")
    assert await store.decide(ch.id, first.id, ch.version, decision)
    second = await message(store, text="new")
    clock.advance()
    assert [item.id for item in await store.unread(ch.id)] == [second.id]
    assert first.id not in (await store.channel(ch.id)).runtime["pending"]
    assert not await store.decide(ch.id, first.id, ch.version, {"passed": True, "reason": "obsolete"})
    assert await store.decide(ch.id, second.id, ch.version, {"passed": True, "reason": "latest"})
    assert (await store.begin_round(ch.id))["observation_ids"] == [second.id]


@pytest.mark.asyncio
async def test_begin_round_retires_unfrozen_superseded_pass(store):
    ch = await channel(store)
    first = await message(store, text="old")
    assert await store.decide(ch.id, first.id, ch.version, {"passed": True, "reason": "old"})
    second = await message(store, text="new")
    assert await store.begin_round(ch.id) is None
    assert (await store.channel(ch.id)).runtime["pending"] == {}
    assert [item.id for item in await store.unread(ch.id)] == [second.id]


@pytest.mark.asyncio
async def test_revision_replacement_never_rewrites_already_frozen_round(store):
    ch, items, round_ = await passed_round(store)
    second = await message(store, "0", text="edited")
    assert await store.decide(ch.id, second.id, ch.version, {"passed": True, "reason": "new"})
    assert (await store.begin_round(ch.id))["observation_ids"] == [items[0].id]
    assert set((await store.channel(ch.id)).runtime["pending"]) == {items[0].id, second.id}
    await control(store, round_, "agent.delivered", {"observation_ids": [items[0].id]})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    await control(store, round_, "agent.finish_requested", {"update": "unchanged"})
    assert (await store.begin_round(ch.id))["observation_ids"] == [second.id]


@pytest.mark.asyncio
async def test_test_fixture_rejects_production_named_database_before_connect(monkeypatch):
    monkeypatch.setenv("ARGUS_TEST_DATABASE_URL", "postgresql+asyncpg://unused:unused@127.0.0.1:1/argus")
    fixture = store.__wrapped__()
    with pytest.raises(ValueError, match="ending _test"):
        await anext(fixture)


@pytest.mark.asyncio
async def test_terminal_reconciliation_releases_only_known_stopped_rounds(store):
    ch, items, round_ = await passed_round(store, 2)
    await control(store, round_, "agent.delivered", {"observation_ids": [x.id for x in items]})
    await control(store, round_, "agent.result_submitted", {"items": [{"observation_id": items[0].id, "status": "handled"}]})
    for status in ("unknown", "accepted", "running"):
        await store.reconcile_round(ch.id, round_["wake_id"], status)
        assert await store.begin_round(ch.id) == (await store.channel(ch.id)).runtime["round"]
        assert (await store.begin_round(ch.id))["wake_id"] == round_["wake_id"]
    await store.reconcile_round(ch.id, round_["wake_id"], "failed")
    current = await store.channel(ch.id)
    assert set(current.runtime["pending"]) == {items[1].id}
    assert current.runtime["pending"][items[1].id]["delivery_attempt_count"] == 1
    with pytest.raises(DomainError):
        await store.bound_channel(round_["binding_id"])
    second = await store.begin_round(ch.id)
    assert second["wake_id"] != round_["wake_id"]
    await store.reconcile_round(ch.id, round_["wake_id"], "failed")
    assert (await store.begin_round(ch.id))["wake_id"] == second["wake_id"]
    await store.reconcile_round(ch.id, second["wake_id"], "failed")
    third = await store.begin_round(ch.id)
    await store.reconcile_round(ch.id, third["wake_id"], "failed")
    assert await store.begin_round(ch.id) is None
    retained = (await store.channel(ch.id)).runtime["pending"][items[1].id]
    assert retained["delivery_attempt_count"] == 3
    assert retained["delivery_error"] == "failed"
    assert await store.unread(ch.id) == []


def test_decision_rejects_pass_and_defer():
    with pytest.raises(ValueError):
        Decision(passed=True, reason="contradiction", defer=True)
