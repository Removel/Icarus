"""QQ observation processing and Icarus session dispatch."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import unicodedata
from uuid import uuid4

from apps.argus.src.clients.IcarusClient import GatewayBusinessError, GatewayTransportError
from apps.argus.src.dao.observation_store import ObservationStore, SessionLink
from apps.argus.src.entity.observation import Observation
from apps.argus.src.handler.QQHandler import QQHandler
from apps.argus.src.strategy.QQStrategy import QQStrategy


def _message_text(obs: Observation) -> str:
    def render(part) -> str:
        if part.kind == "text":
            return part.value
        if part.kind == "image":
            return "[图片]"
        return f"[{part.value}]"

    return "".join(render(part) for part in obs.content)[:2000]


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def name_mentioned(bot_names: tuple[str, ...], obs: Observation) -> bool:
    """Whether the plain text names the bot; only text parts are searched."""
    text = _normalize("".join(part.value for part in obs.content if part.kind == "text"))
    return any(_normalize(name) in text for name in bot_names if name.strip())


class QQService:
    def __init__(self, store: ObservationStore, judge, gateway, *, workspace_path: str,
                 reply_hint=QQHandler.reply_hint, max_age_seconds: float = 300,
                 bot_names: tuple[str, ...] = ()) -> None:
        self.store = store
        self.judge = judge
        self.gateway = gateway
        self.workspace_path = workspace_path
        self.reply_hint = reply_hint
        self.max_age_seconds = max_age_seconds
        self.bot_names = tuple(bot_names)
        self._locks: dict[str, asyncio.Lock] = {}

    async def process(self, obs: Observation) -> None:
        if not await self.store.claim(obs.id):
            return
        if datetime.now(UTC) - obs.source.occurred_at > timedelta(seconds=self.max_age_seconds):
            await self.store.mark_silence(obs.id, "expired", QQStrategy.version)
            return
        if bool(getattr(obs, "is_self", False)):
            await self.store.mark_silence(obs.id, "self_message", QQStrategy.version)
            return
        recent = await self.store.recent(
            obs.source.platform, obs.source.account_id, obs.subject.key, limit=10
        )
        link = await self.store.current_link(
            obs.source.platform, obs.source.account_id, obs.subject.key
        )
        chat_type = getattr(obs, "message_type", None)
        state = {
            "platform": obs.source.platform,
            "subject": obs.subject.key,
            "sender": obs.actor.display_name or obs.actor.external_id,
            "content": _message_text(obs),
            "mentioned": bool(getattr(obs, "mentions_bot", False)),
            "current_session": link is not None and link.workspace_path == self.workspace_path,
            "recent_messages": [_message_text(item) for item in recent[:10]],
            "chat_type": chat_type,
            "is_direct": chat_type == "private",
            "bot_names": list(self.bot_names),
            "name_mentioned": name_mentioned(self.bot_names, obs),
        }
        judgement = await self.judge.decide(
            state=state, questions=QQStrategy.questions, question="should_wake",
            outcomes=("wake", "silence"), fallback="silence",
        )
        if judgement.outcome == "silence":
            await self.store.mark_silence(
                obs.id, judgement.reason or "judge_silence", QQStrategy.version
            )
            return
        await self.dispatch(obs, QQStrategy.version)

    async def dispatch(self, obs: Observation, strategy_version: str) -> str | None:
        if await self.store.status(obs.id) in {"submitting", "submitted", "submission_unknown"}:
            return None
        lock = self._locks.setdefault(obs.subject.key, asyncio.Lock())
        async with lock:
            try:
                return await self._dispatch_locked(obs, strategy_version)
            except (GatewayTransportError, GatewayBusinessError):
                await self.store.release_before_submit(obs.id)
                return None
            except ValueError:
                await self.store.mark_failed_before_submit(obs.id, "workspace_identity_mismatch")
                return None

    async def _dispatch_locked(self, obs: Observation, strategy_version: str) -> str | None:
        link = await self.store.current_link(obs.source.platform, obs.source.account_id, obs.subject.key)
        if link is not None and link.workspace_path != self.workspace_path:
            link = None
        if link is not None:
            try:
                session = await self.gateway.session_get(link.workspace_path, link.session_id)
            except GatewayBusinessError as error:
                if error.code != "session_not_found":
                    raise
                link = None
            else:
                if session["workspace_key"] != link.workspace_key:
                    raise ValueError("Gateway workspace identity does not match the recorded link")
        if link is None:
            target = await self.store.pending_session(obs.id)
            session_id = target[1] if target and target[0] == self.workspace_path else "argus-" + uuid4().hex
            if target is None:
                await self.store.reserve_session_creation(obs.id, self.workspace_path, session_id)
            try:
                session = await self.gateway.session_get(self.workspace_path, session_id)
            except GatewayBusinessError as error:
                if error.code != "session_not_found":
                    raise
                try:
                    session = await self.gateway.session_create(self.workspace_path, session_id)
                except GatewayTransportError:
                    session = await self.gateway.session_get(self.workspace_path, session_id)
            link = SessionLink(obs.source.platform, obs.source.account_id, obs.subject.key,
                               self.workspace_path, session["workspace_key"], session["session_id"])
            await self.store.save_link(link)
        submission_id = obs.id
        if not await self.store.reserve_submission(obs.id, link, submission_id, strategy_version):
            return None
        prompt = (
            f"平台: {obs.source.platform}\n会话: {obs.subject.key}\n"
            f"发送者: {obs.actor.display_name or obs.actor.external_id} ({obs.actor.external_id})\n"
            f"消息: {''.join(part.value if part.kind == 'text' else '[图片]' for part in obs.content)}"
            f"{self.reply_hint(obs.subject.key)}"
        )
        try:
            result = await self.gateway.session_submit(link.workspace_path, link.session_id,
                                                       prompt, submission_id)
        except GatewayBusinessError as error:
            if error.code in {"session_not_found", "submission_conflict", "runtime_stopping", "invalid_resource", "resource_unavailable", "-32602"}:
                await self.store.mark_submission_failed(obs.id, error.code)
            else:
                await self.store.mark_submission_unknown(obs.id, error.code)
            return None
        except GatewayTransportError as error:
            await self.store.mark_submission_unknown(obs.id, type(error).__name__)
            return None
        task_id = str(result["task_id"])
        await self.store.mark_submitted(obs.id, task_id)
        return task_id
