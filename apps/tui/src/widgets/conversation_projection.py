"""Ordered, widget-free presentation state for the conversation."""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserCorrection,
    AppendUserMessage,
    CompleteAssistantMessage,
    CompleteThinking,
    FinishTurn,
    UiAction,
    UpdateToolCompleted,
)


@dataclass
class DisplayUnit:
    kind: str
    task_id: str
    text: str = ""
    step: int = 0
    call_id: str = ""
    tool_name: str = ""
    arguments_json: str = "{}"
    status: str = ""
    partial: bool = False
    expanded: bool = False
    completed: bool = False
    output_preview: object = None
    preview_truncated: bool = False
    full_result_available: bool = False
    preview_error: str | None = None
    error: str | None = None
    applied_before_step: int | None = None
    children: list[DisplayUnit] = field(default_factory=list)


@dataclass
class Turn:
    task_id: str
    text: str
    units: list[DisplayUnit] = field(default_factory=list)


class ConversationProjection:
    """Retain action semantics without retaining Textual widgets."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.turns: list[Turn] = []
        self.units: list[DisplayUnit] = []
        self._task_turns: dict[str, Turn] = {}
        self._task_indices: dict[str, int] = {}
        self._run_cards: dict[str, DisplayUnit] = {}
        self._candidates: dict[tuple[str, int], DisplayUnit] = {}
        self._candidate_completed: set[tuple[str, int]] = set()
        self._thinking: dict[tuple[str, int], DisplayUnit] = {}
        self._completed_thinking: set[tuple[str, int]] = set()
        self._tools: dict[tuple[str, str], DisplayUnit] = {}

    def snapshot_for_turn(self, index: int) -> list[DisplayUnit]:
        return self.turns[index].units

    def turn_index(self, task_id: str) -> int | None:
        return self._task_indices.get(task_id)

    def _place(self, unit: DisplayUnit, *, after: DisplayUnit | None = None) -> None:
        turn = self._task_turns.get(unit.task_id)
        if turn is not None:
            container = turn.units
        else:
            container = self.units
        if after is not None and after in container:
            container.insert(container.index(after) + 1, unit)
        else:
            container.append(unit)
        if turn is not None:
            next_turn = self._task_indices[unit.task_id] + 1
            if next_turn < len(self.turns):
                next_user = self.turns[next_turn].units[0]
                self.units.insert(self.units.index(next_user), unit)
            else:
                self.units.append(unit)

    def _ensure_card(self, task_id: str) -> DisplayUnit:
        card = self._run_cards.get(task_id)
        if card is None:
            card = DisplayUnit("run_card", task_id)
            self._run_cards[task_id] = card
            live = next(
                (
                    item for (candidate_task, step), item in self._candidates.items()
                    if candidate_task == task_id
                    and (candidate_task, step) not in self._candidate_completed
                    and item.kind == "assistant"
                ),
                None,
            )
            turn = self._task_turns.get(task_id)
            container = turn.units if turn else self.units
            if live is not None and live in container:
                container.insert(container.index(live), card)
                if turn is not None:
                    self.units.insert(self.units.index(live), card)
            else:
                self._place(card)
        return card

    def _ensure_candidate(self, task_id: str, step: int) -> DisplayUnit:
        key = task_id, step
        candidate = self._candidates.get(key)
        if candidate is None:
            candidate = DisplayUnit("assistant", task_id, step=step)
            self._candidates[key] = candidate
            self._place(candidate, after=self._run_cards.get(task_id))
        return candidate

    def _downgrade(self, task_id: str, step: int) -> None:
        key = task_id, step
        candidate = self._candidates.get(key)
        if candidate is None or key in self._candidate_completed:
            return
        if candidate.kind == "progress":
            return
        turn = self._task_turns.get(task_id)
        container = turn.units if turn else self.units
        if candidate in container:
            container.remove(candidate)
            if turn is not None:
                self.units.remove(candidate)
        candidate.kind = "progress"
        self._ensure_card(task_id).children.append(candidate)

    def _advance(self, task_id: str, step: int) -> None:
        for old_task, old_step in tuple(self._candidates):
            if old_task == task_id and old_step < step:
                self._downgrade(old_task, old_step)

    def _remove_empty_card(self, task_id: str) -> None:
        card = self._run_cards.get(task_id)
        if card is not None and not card.children:
            self._run_cards.pop(task_id)
            turn = self._task_turns.get(task_id)
            if turn is not None and card in turn.units:
                turn.units.remove(card)
            if card in self.units:
                self.units.remove(card)

    def apply(self, action: UiAction) -> bool:
        if isinstance(action, AppendUserMessage):
            turn = Turn(action.task_id, action.text)
            self.turns.append(turn)
            self._task_turns[action.task_id] = turn
            self._task_indices[action.task_id] = len(self.turns) - 1
            self._place(DisplayUnit("user", action.task_id, action.text))
        elif isinstance(action, AppendAssistantDelta):
            key = action.task_id, action.step
            if key not in self._candidate_completed:
                self._advance(*key)
                self._ensure_candidate(*key).text += action.text
        elif isinstance(action, CompleteAssistantMessage):
            key = action.task_id, action.step
            if key not in self._candidate_completed:
                self._advance(*key)
                self._ensure_candidate(*key).text = action.text
                self._ensure_candidate(*key).completed = True
                self._candidate_completed.add(key)
                card = self._run_cards.pop(action.task_id, None)
                if card is not None and not card.children:
                    turn = self._task_turns.get(action.task_id)
                    if turn is not None and card in turn.units:
                        turn.units.remove(card)
                    if card in self.units:
                        self.units.remove(card)
        elif isinstance(action, AppendThinkingDelta):
            key = action.task_id, action.step
            if key not in self._completed_thinking:
                self._advance(*key)
                thinking = self._thinking.get(key)
                if thinking is None:
                    thinking = DisplayUnit("thinking", action.task_id, step=action.step)
                    self._thinking[key] = thinking
                    self._ensure_card(action.task_id).children.append(thinking)
                thinking.text += action.text
        elif isinstance(action, CompleteThinking):
            key = action.task_id, action.step
            if key not in self._completed_thinking:
                self._advance(*key)
                thinking = self._thinking.get(key)
                if thinking is None:
                    thinking = DisplayUnit("thinking", action.task_id, step=action.step)
                    self._thinking[key] = thinking
                    self._ensure_card(action.task_id).children.append(thinking)
                thinking.text = action.text
                thinking.partial = action.partial
                self._completed_thinking.add(key)
        elif isinstance(action, AppendUserCorrection):
            self._ensure_card(action.task_id).children.append(
                DisplayUnit("correction", action.task_id, action.text,
                            applied_before_step=action.applied_before_step)
            )
        elif isinstance(action, AppendToolStarted):
            self._advance(action.task_id, action.step)
            self._downgrade(action.task_id, action.step)
            tool = DisplayUnit("tool", action.task_id, step=action.step,
                               call_id=action.call_id, tool_name=action.tool_name,
                               arguments_json=action.arguments_json, status="running")
            self._tools[action.task_id, action.call_id] = tool
            self._ensure_card(action.task_id).children.append(tool)
        elif isinstance(action, UpdateToolCompleted):
            self._advance(action.task_id, action.step)
            self._downgrade(action.task_id, action.step)
            key = action.task_id, action.call_id
            tool = self._tools.get(key)
            if tool is None:
                tool = DisplayUnit("tool", action.task_id, step=action.step,
                                   call_id=action.call_id, tool_name=action.tool_name)
                self._tools[key] = tool
                self._ensure_card(action.task_id).children.append(tool)
            tool.status = "completed" if action.success else "failed"
            tool.error = action.error
            tool.output_preview = action.output_preview
            tool.preview_truncated = action.preview_truncated
            tool.full_result_available = action.full_result_available
            tool.preview_error = action.preview_error
        elif isinstance(action, AppendError):
            self._place(DisplayUnit("error", action.task_id, action.message,
                                    status=action.error_type))
        elif isinstance(action, FinishTurn):
            if action.status == "interrupted":
                for (task_id, _), tool in self._tools.items():
                    if task_id == action.task_id and tool.status == "running":
                        tool.status = "interrupted"
            for task_id, step in tuple(self._candidates):
                if task_id == action.task_id:
                    self._downgrade(task_id, step)
                    self._candidates.pop((task_id, step), None)
                    self._candidate_completed.discard((task_id, step))
            if action.status in {"failed", "cancelled", "interrupted"}:
                self._place(DisplayUnit("turn_status", action.task_id,
                                        status=action.status))
            self._remove_empty_card(action.task_id)
        else:
            return False
        return True
