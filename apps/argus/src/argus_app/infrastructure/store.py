"""Two-table transactional fact store. PostgreSQL is the production dialect.

Channel row locks serialize cursors, bounded work queues, and round receipts. Control
rows hold immutable requests and their fixed results; notifications need not survive.
"""
from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import (BigInteger, Column, Identity, Integer, JSON, MetaData, String,
                        Table, UniqueConstraint, and_, exists, or_, select, text, update)
from sqlalchemy.ext.asyncio import create_async_engine

from argus_app.contracts.models import Channel, Decision, DiscoveryDecision, DomainError, Observation
from argus_app.contracts.commit import CommitPayload
from pydantic import ValidationError

metadata = MetaData()
observed_items = Table(
    "observed_items", metadata,
    Column("id", String(36), primary_key=True),
    Column("position", BigInteger, Identity(), nullable=False, unique=True),
    Column("source_key", String, nullable=False),
    Column("message_id", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("observed_at", String, nullable=False),
    Column("content", JSON, nullable=False),
    Column("detail", JSON, nullable=False),
    UniqueConstraint("source_key", "message_id", "revision"),
)
channels = Table(
    "channels", metadata,
    Column("id", String(36), primary_key=True),
    Column("title", String, nullable=False),
    Column("goal", String, nullable=False),
    Column("progress", String, nullable=False),
    Column("lifecycle", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("subscriptions", JSON, nullable=False),
    Column("rule_path", String, nullable=False),
    Column("rule_hash", String, nullable=False),
    Column("runtime", JSON, nullable=False),
)
MESSAGE = "message.observed"
CONTROL_KINDS = {'agent.delivered', 'rule.check_requested', 'agent.commit_requested'}


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DomainError("invalid_json") from exc


def observation(row):
    return Observation.model_validate({key: row[key] for key in Observation.model_fields})


def channel(row):
    return Channel.model_validate(dict(row))


def latest(bounds=None):
    newer = observed_items.alias("newer")
    query = select(newer.c.id).where(
        newer.c.source_key == observed_items.c.source_key,
        newer.c.message_id == observed_items.c.message_id,
        newer.c.revision > observed_items.c.revision)
    if bounds is not None:
        query = query.where(or_(*[and_(newer.c.source_key == source,
                                      newer.c.position <= position)
                                  for source, position in bounds.items()]))
    return ~exists(query)


class Store:
    max_pending = 256

    def __init__(self, database_url: str):
        self.engine = create_async_engine(database_url, pool_pre_ping=True)

    async def start(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def close(self):
        await self.engine.dispose()

    async def ingest(self, source_key: str, message_id: str, kind: str, content: dict,
                     detail: dict | None = None) -> Observation:
        if not source_key or not message_id or not kind or not isinstance(content, dict):
            raise DomainError("invalid_observation")
        detail = copy.deepcopy(detail or {})
        if any(key in detail for key in ("result", "_request_hash", "discovery")):
            raise DomainError("reserved_detail")
        serialized = canonical({"kind": kind, "content": content, "detail": detail})
        if len(serialized.encode()) > 1024 * 1024:
            raise DomainError("observation_too_large")
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        async with self.engine.begin() as conn:
            # Serialize the whole source through commit, not merely each message:
            # otherwise a faster later insert can advance its cursor past an
            # uncommitted earlier sequence position. Also protects first revisions.
            await conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                               {"key": source_key})
            old = (await conn.execute(select(observed_items).where(
                observed_items.c.source_key == source_key,
                observed_items.c.message_id == message_id).order_by(
                observed_items.c.revision.desc()).limit(1))).mappings().first()
            if old:
                if kind != MESSAGE or old["kind"] != MESSAGE:
                    if old["detail"].get("_request_hash") != fingerprint:
                        raise DomainError("request_conflict")
                    return observation(old)
                if canonical(old["content"]) == canonical(content):
                    return observation(old)
            if kind != MESSAGE:
                detail["_request_hash"] = fingerprint
            row = dict(id=str(uuid4()), source_key=source_key, message_id=message_id,
                       kind=kind, revision=old["revision"] + 1 if old else 1,
                       observed_at=datetime.now(timezone.utc).isoformat(),
                       content=copy.deepcopy(content), detail=detail)
            saved = (await conn.execute(observed_items.insert().values(**row).returning(observed_items))).mappings().one()
            return observation(saved)

    async def observation(self, id: str) -> Observation:
        async with self.engine.connect() as conn:
            row = await self._observation(conn, id)
            return observation(row)

    async def _observation(self, conn, id, lock=False):
        query = select(observed_items).where(observed_items.c.id == id)
        if lock:
            query = query.with_for_update()
        row = (await conn.execute(query)).mappings().first()
        if row is None:
            raise DomainError("observation_not_found")
        return row

    async def observations(self, source_keys: list[str], limit: int = 20,
                           before: str | None = None, text: str | None = None,
                           bounds: dict | None = None) -> list[Observation]:
        if not source_keys:
            return []
        query = select(observed_items).where(observed_items.c.kind == MESSAGE,
                    observed_items.c.source_key.in_(source_keys))
        async with self.engine.connect() as conn:
            positions = None
            if before:
                boundary = (await conn.execute(select(observed_items.c.position).where(observed_items.c.id == before))).scalar_one_or_none()
                if boundary is not None:
                    query = query.where(observed_items.c.position < boundary)
                else:
                    query = query.where(observed_items.c.observed_at < before)
            if text is not None:
                query = query.where(observed_items.c.content["text"].as_string().contains(text, autoescape=True))
            if bounds is not None:
                scopes = []
                positions = {}
                for source in source_keys:
                    value = bounds.get(source)
                    if value is None:
                        continue
                    if isinstance(value, dict):
                        value = value.get("cursor", value.get("position"))
                    if value is not None:
                        try:
                            pos = int(value)
                        except (TypeError, ValueError):
                            bound = await self._observation(conn, str(value))
                            pos = bound["position"]
                        positions[source] = pos
                        scopes.append(and_(observed_items.c.source_key == source, observed_items.c.position <= pos))
                if not scopes:
                    return []
                query = query.where(or_(*scopes))
            rows = (await conn.execute(query.where(latest(positions)).order_by(observed_items.c.position.desc()).limit(self._limit(limit, 200)))).mappings().all()
            return [observation(row) for row in rows]

    async def create_channel(self, title: str, goal: str, source_keys: list[str],
                             rule_path: str = "", rule_hash: str = "",
                             channel_id: str | None = None) -> Channel:
        ch = Channel(id=channel_id or str(uuid4()), title=title, goal=goal,
                     subscriptions={key: None for key in source_keys},
                     rule_path=rule_path, rule_hash=rule_hash,
                     runtime={"pending": {}, "round": None})
        async with self.engine.begin() as conn:
            await self._lock_subscriptions(conn)
            await conn.execute(channels.insert().values(**ch.model_dump()))
        return ch

    async def channel(self, id: str) -> Channel:
        async with self.engine.connect() as conn:
            return channel(await self._channel(conn, id))

    async def channels(self) -> list[Channel]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(channels).order_by(channels.c.id))).mappings().all()
            return [channel(row) for row in rows]

    async def _channel(self, conn, id, lock=False):
        query = select(channels).where(channels.c.id == id)
        if lock:
            query = query.with_for_update()
        row = (await conn.execute(query)).mappings().first()
        if row is None:
            raise DomainError("channel_not_found")
        return copy.deepcopy(dict(row))

    async def _save(self, conn, ch, publish=False):
        # Version is a business-configuration CAS, never an operational counter.
        if publish:
            ch["version"] += 1
        await conn.execute(update(channels).where(channels.c.id == ch["id"]).values(**ch))

    @staticmethod
    def _limit(value, maximum):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise DomainError("invalid_limit")
        return min(value, maximum)

    def _unread_query(self, ch, source=None):
        scopes = [and_(observed_items.c.source_key == key,
                       observed_items.c.position > int(cursor or 0))
                  for key, cursor in ch["subscriptions"].items() if source is None or key == source]
        return select(observed_items).where(observed_items.c.kind == MESSAGE,
                    or_(*scopes) if scopes else False, latest()).order_by(observed_items.c.position)

    def _retryable(self, ch, id, entry):
        current = ch["runtime"].get("round")
        if current and id in current["observation_ids"]:
            return False
        if entry["channel_version"] < ch["version"]:
            return True
        return (entry["status"] in {"deferred", "error"}
                and entry["attempt_count"] < 3
                and entry["next_retry_at"] <= datetime.now(timezone.utc).isoformat())

    async def _retire_pending(self, conn, ch):
        """Discard only non-frozen obsolete revisions or removed subscriptions."""
        pending = ch["runtime"]["pending"]
        current = ch["runtime"].get("round")
        frozen = set(current["observation_ids"]) if current else set()
        candidates = set(pending) - frozen
        if not candidates:
            return False
        live = set((await conn.execute(select(observed_items.c.id).where(
            observed_items.c.id.in_(candidates),
            observed_items.c.source_key.in_(ch["subscriptions"]), latest()))).scalars())
        retired = candidates - live
        for id in retired:
            pending.pop(id)
        return bool(retired)

    async def unread(self, channel_id: str, limit: int = 50) -> list[Observation]:
        async with self.engine.begin() as conn:
            ch = await self._channel(conn, channel_id, lock=True)
            if await self._retire_pending(conn, ch):
                await self._save(conn, ch)
            if ch["lifecycle"] != "active":
                return []
            limit = self._limit(limit, 256)
            retry_ids = [id for id, entry in ch["runtime"]["pending"].items()
                         if self._retryable(ch, id, entry)]
            retry = (await conn.execute(select(observed_items).where(observed_items.c.id.in_(retry_ids), observed_items.c.source_key.in_(ch["subscriptions"]), latest()).order_by(observed_items.c.position).limit(limit))).mappings().all() if retry_ids else []
            remaining = limit - len(retry)
            rows = (await conn.execute(self._unread_query(ch).limit(remaining))).mappings().all() if remaining else []
            return [observation(row) for row in [*retry, *rows]]

    async def decide(self, channel_id: str, observation_id: str, expected_version: int,
                     decision: dict) -> bool:
        error = decision.get("error")
        parsed = None if error else Decision.model_validate(decision)
        async with self.engine.begin() as conn:
            ch = await self._channel(conn, channel_id, lock=True)
            row = await self._observation(conn, observation_id)
            if ch["version"] != expected_version or ch["lifecycle"] != "active" or row["kind"] != MESSAGE or row["source_key"] not in ch["subscriptions"]:
                return False
            if await self._retire_pending(conn, ch):
                await self._save(conn, ch)
            if (await conn.execute(select(observed_items.c.id).where(
                    observed_items.c.id == observation_id, latest()))).scalar_one_or_none() is None:
                return False
            pending = ch["runtime"]["pending"]
            round_ = ch["runtime"].get("round")
            if round_ and observation_id in round_["observation_ids"]:
                return False
            entry = pending.get(observation_id)
            retry = entry is not None and self._retryable(ch, observation_id, entry)
            if entry is not None and not retry:
                return False
            if not retry:
                first = (await conn.execute(self._unread_query(ch, row["source_key"]).limit(1))).mappings().first()
                if first is None or first["id"] != observation_id:
                    return False
            keep = bool(error) or parsed.passed or parsed.defer
            if keep and observation_id not in pending and len(pending) >= self.max_pending:
                raise DomainError("pending_capacity")
            if keep:
                status = "error" if error else "passed" if parsed.passed else "deferred"
                value = {"error": str(error)[:2048]} if error else parsed.model_dump()
                if len(canonical(value)) > 8192:
                    raise DomainError("decision_too_large")
                attempts = (entry or {}).get("attempt_count", 0) if (entry or {}).get("channel_version") == ch["version"] else 0
                attempts = attempts + 1 if status in {"deferred", "error"} else 0
                pending[observation_id] = {"status": status, "decision": value,
                    "channel_version": ch["version"], "attempt_count": attempts,
                    "delivery_attempt_count": (entry or {}).get("delivery_attempt_count", 0),
                    "delivery_error": (entry or {}).get("delivery_error", ""),
                    "next_retry_at": (datetime.now(timezone.utc) +
                        timedelta(seconds=5 if attempts == 1 else 30)).isoformat()
                    if attempts else None}
            else:
                pending.pop(observation_id, None)
            previous = int(ch["subscriptions"][row["source_key"]] or 0)
            ch["subscriptions"][row["source_key"]] = str(max(previous, row["position"]))
            await self._save(conn, ch)
            return True

    async def begin_round(self, channel_id: str, limit: int = 20) -> dict | None:
        limit = self._limit(limit, 20)
        async with self.engine.begin() as conn:
            ch = await self._channel(conn, channel_id, lock=True)
            if await self._retire_pending(conn, ch):
                await self._save(conn, ch)
            current = ch["runtime"].get("round")
            if current and not current["closed"]:
                return current
            if ch["lifecycle"] != "active":
                return None
            candidates = [id for id, value in ch["runtime"]["pending"].items()
                          if value["status"] == "passed" and value["channel_version"] == ch["version"]
                          and value.get("delivery_attempt_count", 0) < 3]
            ids = []
            size = 2  # serialized list brackets; same ensure_ascii default as Gateway
            rejected = False
            for id in candidates:
                row = await self._observation(conn, id)
                item_size = len(json.dumps(observation(row).model_dump(mode="json")).encode())
                if item_size + 2 > 384 * 1024:
                    entry = ch["runtime"]["pending"][id]
                    entry["delivery_attempt_count"] = 3
                    entry["delivery_error"] = "delivery_item_too_large"
                    rejected = True
                    continue
                addition = item_size + (2 if ids else 0)
                if len(ids) >= limit or size + addition > 384 * 1024:
                    break
                ids.append(id)
                size += addition
            if not ids:
                if rejected:
                    await self._save(conn, ch)
                return None
            current = dict(wake_id=str(uuid4()), binding_id=str(uuid4()), observation_ids=ids,
                           channel_version=ch["version"],
                           delivered=[], confirmed={}, session_id=None, task_id=None,
                           update_request_id=None, closed=False)
            ch["runtime"]["round"] = current
            await self._save(conn, ch)
            return current

    async def reconcile_round(self, channel_id: str, wake_id: str, status: str) -> None:
        """Release only a remotely known-stopped task; uncertainty keeps binding.

        Confirmed items are complete. Unconfirmed items remain passed for bounded
        redelivery, never silently acknowledged. Exhausted delivery state remains
        visible in pending and continues to consume capacity until resolved.
        """
        if status not in ("failed", "cancelled", "completed"):
            return
        async with self.engine.begin() as conn:
            ch = await self._channel(conn, channel_id, lock=True)
            current = ch["runtime"].get("round")
            if not current or current["wake_id"] != wake_id:
                return
            pending = ch["runtime"]["pending"]
            for id in current['observation_ids']:
                if id in pending:
                    entry = pending[id]
                    entry['delivery_attempt_count'] = min(3, entry.get('delivery_attempt_count', 0) + 1)
                    entry['delivery_error'] = status
            ch["runtime"]["round"] = None
            await self._retire_pending(conn, ch)
            if ch["lifecycle"] == "completed":
                ch["subscriptions"] = {}
                ch["runtime"]["pending"] = {}
            await self._save(conn, ch)

    async def set_round_delivery(self, channel_id: str, wake_id: str, session_id: str,
                                 task_id: str) -> None:
        async with self.engine.begin() as conn:
            ch = await self._channel(conn, channel_id, lock=True)
            current = ch["runtime"].get("round")
            if not current or current["closed"] or current["wake_id"] != wake_id:
                raise DomainError("stale_round")
            if current["session_id"] is not None and (current["session_id"] != session_id or current["task_id"] != task_id):
                raise DomainError("delivery_conflict")
            if current["session_id"] == session_id and current["task_id"] == task_id:
                return
            current.update(session_id=session_id, task_id=task_id)
            await self._save(conn, ch)

    async def _bound(self, conn, binding_id, lock=False):
        if not isinstance(binding_id, str) or not binding_id:
            raise DomainError("invalid_binding")
        query = select(channels).where(channels.c.runtime["round"]["binding_id"].as_string() == binding_id)
        if lock:
            query = query.with_for_update()
        row = (await conn.execute(query)).mappings().first()
        if row is None or row["runtime"]["round"].get("closed"):
            raise DomainError("invalid_binding")
        return copy.deepcopy(dict(row))

    async def bound_channel(self, binding_id: str) -> Channel:
        async with self.engine.connect() as conn:
            return channel(await self._bound(conn, binding_id))

    async def commit_status(self, binding_id: str, request_id: str) -> dict:
        """Read a durable receipt without creating or resuming a control operation."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(observed_items.c.detail).where(
                observed_items.c.message_id == request_id,
                observed_items.c.kind == 'agent.commit_requested',
                observed_items.c.detail['binding_id'].as_string() == binding_id,
            ))).scalars().all()
        if not rows:
            return {'state': 'not_found'}
        result = rows[0].get('result')
        if result is None:
            return {'state': 'pending'}
        return {'state': 'applied' if result.get('applied') else 'rejected', 'result': result}

    async def apply_control(self, observation_id: str, *, prepared_rule: dict | None = None) -> dict:
        async with self.engine.begin() as conn:
            # Subscription publication and first-Channel creation share this short
            # transaction lock; no model/worker call runs under it.
            await self._lock_subscriptions(conn)
            row = await self._observation(conn, observation_id, lock=True)
            detail = copy.deepcopy(row["detail"])
            if "result" in detail:
                return detail["result"]
            try:
                if row["kind"] == "rule.check_requested":
                    raise DomainError("external_check_required")
                if row["kind"] not in CONTROL_KINDS:
                    raise DomainError("invalid_control")
                ch = await self._bound(conn, detail.get("binding_id"), lock=True)
                # Mutate a copy: rejected multi-item controls cannot partially apply.
                payload = copy.deepcopy(detail.get("payload", row["content"]))
                if not isinstance(payload, dict):
                    raise DomainError("invalid_payload")
                if row['kind'] == 'agent.commit_requested' and 'rule_source' in payload:
                    source = payload.pop('rule_source')
                    if (not isinstance(source, str) or prepared_rule is None
                            or prepared_rule.get('rule_hash') != hashlib.sha256(source.encode()).hexdigest()
                            or 'rule_hash' in payload or 'rule_path' in payload):
                        raise DomainError('unprepared_rule')
                    payload.update(prepared_rule)
                changed = self._apply(ch, row, payload)
                await self._retire_pending(conn, ch)
                await self._save(conn, ch, publish=bool(changed))
                result = {"applied": True, "channel_id": ch["id"], "version": ch["version"]}
                if row['kind'] == 'agent.commit_requested':
                    result.update(committed=True, rule_hash=ch['rule_hash'])
            except DomainError as exc:
                result = {"applied": False, "code": exc.code}
            detail["result"] = result
            await conn.execute(update(observed_items).where(observed_items.c.id == observation_id).values(detail=detail))
            return result

    def _apply(self, ch, row, payload):
        round_ = ch["runtime"]["round"]
        frozen = set(round_["observation_ids"])
        if row['kind'] == 'agent.commit_requested':
            try:
                commit = CommitPayload.model_validate(payload)
            except ValidationError as exc:
                raise DomainError('invalid_commit') from exc
            if commit.expected_version != ch['version']:
                raise DomainError('stale_version')
            ids = [item.observation_id for item in commit.results]
            if set(ids) != frozen or len(ids) != len(frozen):
                raise DomainError('invalid_confirmation')
            if not frozen.issubset(round_['delivered']):
                raise DomainError('events_not_delivered')
            before = {key: copy.deepcopy(ch[key]) for key in
                      ('progress', 'subscriptions', 'rule_path', 'rule_hash', 'lifecycle')}
            values = commit.model_dump(exclude_unset=True, exclude={'results', 'expected_version'})
            if 'rule_path' in values and values['rule_path'] != f"{ch['id']}/{values['rule_hash']}.py":
                raise DomainError('invalid_rule_path')
            sources = values.pop('source_keys', None)
            if sources is not None:
                ch['subscriptions'] = {key: ch['subscriptions'].get(key) for key in sources}
            ch.update(values)
            for ref in frozen:
                ch['runtime']['pending'].pop(ref, None)
            ch['runtime']['round'] = None
            if ch['lifecycle'] == 'completed':
                ch['subscriptions'] = {}
                ch['runtime']['pending'] = {}
            return any(ch[key] != value for key, value in before.items())
        if row["kind"] == "agent.delivered":
            refs = payload.get("observation_ids")
            if not isinstance(refs, list) or not refs or not all(isinstance(x, str) and x in frozen for x in refs):
                raise DomainError("invalid_delivery")
            round_["delivered"] = sorted(set(round_["delivered"]) | set(refs))
        else:
            raise DomainError('invalid_control')

    async def complete_control(self, observation_id: str, result: dict) -> dict:
        if (not isinstance(result, dict) or type(result.get("applied")) is not bool
                or len(canonical(result)) > 65536):
            raise DomainError("invalid_result")
        async with self.engine.begin() as conn:
            row = await self._observation(conn, observation_id, lock=True)
            if (row["kind"] not in CONTROL_KINDS
                    or (row["kind"] != "rule.check_requested" and result["applied"])):
                raise DomainError("invalid_control")
            detail = copy.deepcopy(row["detail"])
            if "result" in detail:
                return detail["result"]
            # External validation may reject without mutating Channel. A successful
            # check requires a live binding; other successes use apply_control's gates.
            if result["applied"]:
                await self._bound(conn, detail.get("binding_id"), lock=True)
            detail["result"] = result
            await conn.execute(update(observed_items).where(observed_items.c.id == observation_id).values(detail=detail))
            return result

    @staticmethod
    async def _lock_subscriptions(conn):
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                           {"key": "argus:channel-subscriptions"})

    @staticmethod
    def discovered_channel_id(item: Observation) -> str:
        identity = canonical([item.source_key, item.message_id])
        return str(uuid5(NAMESPACE_URL, "icarus:argus:discovery:" + identity))

    async def discovery_candidates(self, limit: int = 20) -> list[Observation]:
        state = observed_items.c.detail["discovery"]["status"].as_string()
        retry_at = observed_items.c.detail["discovery"]["next_retry_at"].as_string()
        due = or_(state.is_(None), and_(state.in_(["error", "deferred"]),
                    retry_at <= datetime.now(timezone.utc).isoformat()))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(observed_items).where(
                observed_items.c.kind == MESSAGE, latest(), due
            ).order_by(observed_items.c.position).limit(self._limit(limit, 50)))).mappings().all()
            return [observation(row) for row in rows]

    async def record_discovery(self, observation_id: str, decision: dict,
                               rule_path: str = "", rule_hash: str = "",
                               candidate_versions: dict | None = None) -> str | None:
        error = decision.get('error')
        parsed = None if error else DiscoveryDecision.model_validate(decision)
        async with self.engine.begin() as conn:
            await self._lock_subscriptions(conn)
            row = await self._observation(conn, observation_id, lock=True)
            if row['kind'] != MESSAGE:
                raise DomainError('invalid_discovery_event')
            detail = copy.deepcopy(row['detail'])
            previous = detail.get('discovery', {})
            if previous.get('status') in {'created', 'existing', 'ignored', 'exhausted'}:
                return previous.get('channel_id') if previous.get('status') == 'created' else None
            if previous.get('next_retry_at', '') > datetime.now(timezone.utc).isoformat():
                return None
            owners = (await conn.execute(select(channels).where(
                channels.c.lifecycle.in_(['active', 'paused'])))).mappings().all()
            owners = [dict(x) for x in owners if row['source_key'] in x['subscriptions']]
            if candidate_versions is not None and {x['id']: x['version'] for x in owners} != candidate_versions:
                error = 'discovery_context_changed'
            is_latest = (await conn.execute(select(observed_items.c.id).where(
                observed_items.c.id == observation_id, latest()))).scalar_one_or_none() is not None
            channel_id = None
            if not is_latest:
                result = {'status': 'ignored', 'reason': 'superseded_revision'}
            elif error or parsed.action == 'defer':
                attempts = min(3, previous.get('attempt_count', 0) + 1)
                result = {'status': 'exhausted' if attempts == 3 else 'error' if error else 'deferred',
                          'attempt_count': attempts, 'error_code': str(error or parsed.reason)[:512],
                          'next_retry_at': (datetime.now(timezone.utc) + timedelta(seconds=5 if attempts == 1 else 30)).isoformat()}
            elif parsed.action == 'ignore':
                result = {'status': 'ignored', 'reason': parsed.reason[:512]}
            elif parsed.action == 'existing':
                if parsed.channel_id not in {x['id'] for x in owners}:
                    raise DomainError('discovery_candidate_forbidden')
                result = {'status': 'existing', 'channel_id': parsed.channel_id, 'reason': parsed.reason[:512]}
            else:
                message = str(row['content'].get('text', '')).strip()
                normalized = ' '.join(unicodedata.normalize('NFKC', message).casefold().split())
                topic_key = hashlib.sha256(normalized.encode()).hexdigest()
                same = next((x for x in owners if x['runtime'].get('discovery', {}).get('topic_key') == topic_key), None)
                if same:
                    result = {'status': 'existing', 'channel_id': same['id'], 'reason': 'active topic already exists'}
                    channel_id = same['id']
                else:
                    if not rule_path or not rule_hash:
                        raise DomainError('discovery_rule_required')
                    item = observation(row)
                    channel_id = self.discovered_channel_id(item)
                    existing = (await conn.execute(select(channels.c.id).where(channels.c.id == channel_id))).scalar_one_or_none()
                    if existing is None:
                        pending = {item.id: {'status': 'passed', 'decision': {'passed': True, 'reason': parsed.reason},
                                   'channel_version': 1, 'attempt_count': 0, 'next_retry_at': None,
                                   'delivery_attempt_count': 0, 'delivery_error': ''}}
                        ch = Channel(id=channel_id, title=(message or 'New observed request')[:120],
                                     goal=(message or parsed.reason)[:4000],
                                     subscriptions={item.source_key: str(row['position'])},
                                     rule_path=rule_path, rule_hash=rule_hash,
                                     runtime={'pending': pending, 'round': None,
                                              'discovery': {'topic_key': topic_key, 'source_key': item.source_key}})
                        await conn.execute(channels.insert().values(**ch.model_dump()))
                    result = {'status': 'created', 'channel_id': channel_id}
            detail['discovery'] = result
            await conn.execute(update(observed_items).where(observed_items.c.id == observation_id).values(detail=detail))
            return channel_id

    async def pending_controls(self) -> list[Observation]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(observed_items).where(observed_items.c.kind.in_(CONTROL_KINDS)).order_by(observed_items.c.position))).mappings().all()
            return [observation(row) for row in rows if "result" not in row["detail"]]
