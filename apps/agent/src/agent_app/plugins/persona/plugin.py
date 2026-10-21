"""Global persona assets, session-local observation and prompt contributions."""

import asyncio
from collections import OrderedDict, deque
import math
import os

from agent_app.contracts.prompt import PromptContributionEvent, PromptSection
from agent_app.plugins.persona.judge import Judge, bounded_recent, create_judge, NullJudge
from agent_app.plugins.persona.relationship import RelationshipReader
from agent_app.plugins.persona.store import PersonaStore
from agent_app.plugins.persona.prompts import DEFENSE, EVOLUTION_GUIDANCE, POLICY, REANCHOR, STRONG_DEFENSE
from icarus.core.models.messages import TextPart
from icarus.core.tools.result_budget import conservative_token_count
from icarus.core.run_control import TaskChannelStatus
from agent_app.plugins.persona.config import PersonaConfig
from icarus.core.agents import AgentCompletedEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.plugin import BasePlugin, handles


class PersonaPlugin(BasePlugin):
    config_schema = PersonaConfig

    def __init__(self, plugin_id, *, config, data_dir, session_id, logger, task_channels=None):
        super().__init__(plugin_id)
        self.config = config
        self.data_dir = data_dir
        self.session_id = session_id
        self.task_channels = task_channels
        self.logger = logger
        self.store = PersonaStore(data_dir, config.persona_id, config)
        self.judge: Judge = NullJudge()
        endpoint = os.getenv("ICARUS_RELATIONSHIP_ENDPOINT")
        connection = config if endpoint is None else PersonaConfig.model_validate({**config.model_dump(), "relationship_endpoint": endpoint})
        self.relationship = RelationshipReader(connection, api_token=os.getenv("RELA_MEM_API_TOKEN", "").strip())
        self.document = None
        self._sections: tuple[PromptSection, ...] = ()
        self._accepting = True
        self.turn_count = 0
        self._seen_inputs = OrderedDict()
        self._seen_completed = OrderedDict()
        self._assistant: deque[str] = deque(maxlen=5)
        self._recent = []
        self._evolution_lock = asyncio.Lock()
        self._last_evolution_turn = None

    def build_tools(self):
        from agent_app.plugins.persona.tools import PersonaEvolveTool
        return (PersonaEvolveTool(self),)

    async def start(self):
        self.judge = create_judge(self.config)

    async def on_session_start(self):
        self.document = self.store.load()
        relationship = await self.relationship.load()
        policy = POLICY
        if self.config.jev_enabled and not isinstance(self.judge, NullJudge):
            policy += EVOLUTION_GUIDANCE
        values = (("persona-01-identity", self.document.identity_text),
                  ("persona-02-relationship", relationship),
                  ("persona-03-evolved", self.document.evolved_text),
                  ("persona-04-policy", policy))
        self._sections = tuple(PromptSection(key, "persona", text) for key, text in values if text)

    def static_prompt_sections(self):
        return self._sections

    async def quiesce(self):
        self._accepting = False

    async def stop(self):
        await self.quiesce()
        await self.judge.aclose()
        await self.relationship.aclose()

    @staticmethod
    def _remember(seen, task_id):
        if task_id in seen:
            return False
        seen[task_id] = None
        while len(seen) > 128:
            seen.popitem(last=False)
        return True

    async def _confidence(self, operation):
        try:
            value = await asyncio.wait_for(operation, self.config.jev_timeout_seconds)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Invalid judge score")
            return value
        except Exception as error:
            self.logger.warning("Persona detection skipped: error_type=%s", type(error).__name__)
            return None

    @handles(UserInputEvent)
    async def on_input(self, source, event):
        if not event.task_id or not self._remember(self._seen_inputs, event.task_id):
            return
        sections: tuple[PromptSection, ...] = ()
        if self.task_channels is not None:
            channel = self.task_channels.get(event.task_id)
            if channel is None or channel.status not in {
                    TaskChannelStatus.ACCEPTED, TaskChannelStatus.PREPARING_CONTEXT}:
                await self.publish(PromptContributionEvent(task_id=event.task_id, status="completed"))
                return
        try:
            self.turn_count += 1
            self._recent = bounded_recent([*self._recent, ("user", event.prompt)])
            if self._accepting and self.config.jev_enabled and not isinstance(self.judge, NullJudge):
                operations = [self._confidence(self.judge.attack_confidence(event.prompt))]
                drift_due = (self.turn_count % self.config.drift_every_turns == 0
                             and len(self._assistant) >= 3 and self.document is not None)
                if drift_due:
                    assert self.document is not None
                    operations.append(self._confidence(self.judge.drift_confidence(
                        tuple(self._assistant), self.document.drift_reference_text)))
                results = await asyncio.gather(*operations)
                candidates = []
                if results[0] is not None and results[0] >= self.config.attack_defend_threshold:
                    text = STRONG_DEFENSE if results[0] >= self.config.attack_refuse_threshold else DEFENSE
                    candidates.append(PromptSection("persona-defense", "contributor", text))
                if drift_due and results[1] is not None and results[1] >= self.config.drift_threshold:
                    candidates.append(PromptSection("persona-reanchor", "contributor", REANCHOR))
                selected: list[PromptSection] = []
                for section in candidates:
                    proposed = [*selected, section]
                    rendered = "\n\n".join(f"## contributor/{self.plugin_id}/{s.section_id}\n{s.content}" for s in proposed)
                    if conservative_token_count(rendered) > self.config.max_dynamic_tokens:
                        break
                    selected = proposed
                sections = tuple(selected)
        except Exception as error:
            self.logger.warning("Persona contribution degraded: error_type=%s", type(error).__name__)
        await self.publish(PromptContributionEvent(task_id=event.task_id, status="completed", sections=sections))

    @handles(AgentCompletedEvent)
    async def on_completed(self, source, event):
        if not event.task_id or not self._remember(self._seen_completed, event.task_id):
            return
        text = "".join(part.text for part in event.response.message.content if isinstance(part, TextPart))
        if text:
            self._assistant.append(text[:1000])
            while sum(map(len, self._assistant)) > 4000:
                self._assistant.popleft()
            self._recent = bounded_recent([*self._recent, ("assistant", text)])
