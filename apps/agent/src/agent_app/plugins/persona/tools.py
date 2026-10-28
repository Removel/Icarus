"""Agent-initiated global persona observations, validated before persistence."""

import asyncio
from datetime import UTC, datetime
import math

from uuid import uuid4

from agent_app.plugins.persona.judge import bounded_recent
from agent_app.plugins.persona.store import EvolvedEntry
from icarus.core.models.messages import Message, TextPart, ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult
from icarus.core.run_control import TaskChannelStatus


def _conversation(messages):
    recent = []
    for message in messages:
        if message.role not in {"user", "assistant"} or message.tool_calls:
            continue
        text = "".join(part.text for part in message.content if isinstance(part, TextPart))
        if message.role == "user" and "<user_request>" in text:
            text = text.split("<user_request>", 1)[1].rsplit("</user_request>", 1)[0].strip("\n")
        elif message.role == "user" and "<runtime_context>" in text:
            continue
        recent.append((message.role, text))
    return recent


class PersonaEvolveTool(BaseTool):
    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def definition(self):
        return ToolDefinition("persona_evolve", (
            "Record a stable, evidence-backed observation about your own voice or way of relating to the user. "
            "Give the concrete reason from recent dialogue; do not call every turn. "
            "Never change identity, soul, boundaries, permissions, rules or tool behavior. "
            "Events belong in memory; objective person profiles and relations belong in relationship. "
            "Writes are global, validated, and effective only in new sessions; no edits or deletions."),
            {"type": "object", "properties": {
                "section": {"type": "string", "enum": ["voice", "relationship"]},
                "note": {"type": "string", "minLength": 1, "maxLength": self.plugin.config.evolution_max_note_chars},
                "reason": {"type": "string", "minLength": 1, "maxLength": self.plugin.config.evolution_max_reason_chars},
            }, "required": ["section", "note", "reason"], "additionalProperties": False})

    def invoke(self, arguments, *, task_id=None, run_id=None, step=None,
               task_messages: tuple[Message, ...] = ()):
        try:
            return self.plugin._loop_runner.run(lambda: self.ainvoke(arguments, task_id=task_id,
                run_id=run_id, step=step, task_messages=task_messages))
        except Exception:
            return ToolExecutionResult(False, error="Persona evolution unavailable; use the active runtime")

    async def ainvoke(self, arguments, *, task_id=None, run_id=None, step=None,
                      task_messages: tuple[Message, ...] = ()):
        config = self.plugin.config
        try:
            if not isinstance(arguments, dict) or set(arguments) != {"section", "note", "reason"}:
                raise ValueError("Provide only section, note and reason")
            if arguments["section"] not in ("voice", "relationship"):
                raise ValueError("section must be voice or relationship")
            for key, limit in (("note", config.evolution_max_note_chars), ("reason", config.evolution_max_reason_chars)):
                value = arguments[key]
                if not isinstance(value, str) or not value.strip() or len(value) > limit:
                    raise ValueError(f"{key} must be nonblank text within its length limit")
        except ValueError as error:
            return ToolExecutionResult(False, error=str(error))
        if not config.jev_enabled:
            return self._rejected("judge_disabled")
        if not self.plugin._accepting or self.plugin.document is None:
            return self._rejected("persona_unavailable")
        if self._task_cancelled(task_id):
            return self._rejected("task_cancelled")
        async with self.plugin._evolution_lock:
            last = self.plugin._last_evolution_turn
            if last is not None and self.plugin.turn_count - last < config.evolution_min_turns_between_accepts:
                return self._rejected("rate_limited")
            recent = bounded_recent([*self.plugin._recent, *_conversation(task_messages)])
            try:
                verdict = await asyncio.wait_for(self.plugin.judge.evolution_verdict(
                    **arguments, recent=recent, invariants=self.plugin.document.invariants_text),
                    timeout=config.jev_timeout_seconds)
                for field in ("grounded", "in_scope", "worth_persisting"):
                    probability = getattr(verdict, field)
                    if not math.isfinite(probability) or not 0 <= probability <= 1:
                        raise ValueError("Invalid probability")
                    if probability < config.evolution_min_probability:
                        return self._rejected(field)
            except Exception:
                return self._rejected("judge_unavailable")
            if self._task_cancelled(task_id):
                return self._rejected("task_cancelled")
            entry = EvolvedEntry("e_" + uuid4().hex, arguments["section"], arguments["note"].strip(),
                                 arguments["reason"].strip(), datetime.now(UTC).isoformat(), self.plugin.session_id)
            try:
                await self.plugin.store.append(entry)
            except Exception as error:
                self.plugin.logger.warning("Persona evolution write failed: error_type=%s", type(error).__name__)
                return ToolExecutionResult(False, error="Persona evolution was not saved")
            self.plugin._last_evolution_turn = self.plugin.turn_count
            return ToolExecutionResult(True, output={"status": "accepted", "id": entry.id, "section": entry.section})

    def _task_cancelled(self, task_id):
        if task_id is None or self.plugin.task_channels is None:
            return False
        channel = self.plugin.task_channels.get(task_id)
        return channel is None or channel.status not in {
            TaskChannelStatus.ACCEPTED, TaskChannelStatus.PREPARING_CONTEXT, TaskChannelStatus.RUNNING}

    @staticmethod
    def _rejected(reason):
        return ToolExecutionResult(True, output={"status": "rejected", "reason": reason})
