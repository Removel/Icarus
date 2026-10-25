"""Code gates, budgeted facts and optional semantic rule choice."""

import math
from datetime import UTC, datetime

from typesafe_sdk import Noul, NoulAnswer

from ..schemas.observation import SelectedItem, item_text
from ..schemas.judgement import IgnoredSelection, JudgementOutcome
from ..clients.jev_client import JevCallError
from .rule_service import evaluate_mechanical, candidate_semantic_rules
from .observation_access_policy import ObservationAccessPolicy


class JudgementService:
    def __init__(self, rule_set, surface, jev, *, sensitive_keywords=(), max_items=20,
                 max_chars=20000, recent_chars=4000):
        if rule_set.source != surface.source:
            raise ValueError('Rule source does not match surface')
        self.rules, self.surface, self.jev = rule_set, surface, jev
        self.access_policy = ObservationAccessPolicy(sensitive_keywords)
        self.keywords = self.access_policy.keywords
        self.max_items, self.max_chars, self.recent_chars = max_items, max_chars, recent_chars

    def sensitive(self, text):
        return self.access_policy.sensitive(text)

    @staticmethod
    def _window_entry(item, text, *, current, budget):
        speaker = 'Icarus' if item.is_self else (item.sender_name or '')
        speaker = speaker[:max(0, budget - len(text))]
        return {'sender': speaker, 'is_self': item.is_self, 'text': text,
                'sent_at': item.sent_at.isoformat(), 'current': current}

    async def judge(self, items, *, permission_level, recent_context=()):
        selected, ignored, used = [], [], 0
        for item in items[:self.max_items]:
            if item.deleted or item.status != 'new' or item.wake_id is not None:
                continue
            raw = self.surface.text(item)
            reason = ('self_message' if item.is_self else 'sensitive_content' if not self.access_policy.allows(item)
                      else 'permission_denied' if permission_level < 1 else 'unreadable_content' if not raw else None)
            if reason:
                ignored.append(IgnoredSelection(item_ids=(item.id,), reason=reason))
                continue
            if selected and used + len(raw) > self.max_chars:
                break
            excerpt = raw[:self.max_chars - used]
            selected.append(SelectedItem(item=item, excerpt=excerpt, truncated=len(excerpt) < len(raw)))
            used += len(excerpt)
            if used >= self.max_chars:
                break
        detail = {'rule_version': self.rules.version, 'rule_digest': self.rules.digest}

        def outcome(decision='ignore', reason='empty_selection', rule=None):
            return JudgementOutcome(selected=tuple(selected), ignored=tuple(ignored), decision=decision,
                reason=reason, rule_id=None if rule is None else rule.id, decision_detail=detail)

        if not selected:
            return outcome()
        facts = self.surface.build_facts(selected, permission_level=permission_level)
        rule = evaluate_mechanical(self.rules, facts)
        if rule is not None:
            detail.update(kind='rule', rule_id=rule.id)
            return outcome(rule.decision, rule.reason, rule)
        candidates = candidate_semantic_rules(self.rules, facts)
        if not candidates:
            return outcome(reason=self.rules.default_reason)
        if self.jev is None:
            return outcome(reason='jev_disabled')
        window, remaining = [], self.recent_chars
        for item in recent_context:
            if item.deleted or not self.access_policy.allows(item) or remaining <= 0:
                continue
            text = item_text(item)[:remaining]
            entry = self._window_entry(item, text, current=False, budget=remaining)
            remaining -= len(entry['text']) + len(entry['sender'])
            window.append(entry)
        current_remaining = self.max_chars
        for value in selected:
            text = value.excerpt[:current_remaining]
            entry = self._window_entry(value.item, text, current=True, budget=current_remaining)
            current_remaining -= len(entry['text']) + len(entry['sender'])
            window.append(entry)
        state = {'scope_kind': facts['scope_kind'], 'now': datetime.now(UTC).isoformat(),
                 'window': window}
        speak = next((rule for rule in candidates if rule.id == 'speak'), None)
        stay_silent = next((rule for rule in candidates if rule.id == 'stay_silent'), None)
        if speak is None or stay_silent is None:
            return outcome(reason=self.rules.default_reason)
        try:
            response = await self.jev.ask(state=state, questions={
                'can_speak': Noul(
                    instructions=('你在判断 Icarus 现在在这个窗口里说话是否合适/可以。'
                                  '不是只判断消息是不是对 Icarus 说的；如果话题适合 Icarus 参与、'
                                  '群里有开放问题没人接，或者 Icarus 有新的信息可以补充，也可以为是。'
                                  '只有明确不适合、会打断别人，或 Icarus 刚说过且没有新信息时才为否。'),
                    criteria={'true': speak.description, 'false': stay_silent.description}),
                'speak_now': Noul(
                    instructions=('如果 can_speak 为是，判断 Icarus 现在就说是否合适。'
                                  '有人在等回应、话题正在展开、Icarus 的新信息现在有价值时为是；'
                                  '可以再等、会重复或会打断别人时为否。'),
                    criteria={'true': '现在就说：有人在等回应、话题正在展开、Icarus 的新信息现在有价值，且不会打断。',
                              'false': '再等等：可以稍后再说、等对方再提，或者现在说会重复、打断别人。'}),
            })
            can_speak = response.answers['can_speak']
            speak_now = response.answers['speak_now']
            if not isinstance(can_speak, NoulAnswer) or not isinstance(speak_now, NoulAnswer):
                raise ValueError('Invalid semantic answer')
            if (not math.isfinite(can_speak.noul) or not 0 <= can_speak.noul <= 1
                    or not math.isfinite(speak_now.noul) or not 0 <= speak_now.noul <= 1):
                raise ValueError('Invalid semantic probability')
            detail.update(kind='jev', can_speak=can_speak.noul, speak_now=speak_now.noul, failed=False)
            chosen = speak if can_speak.noul >= 0.5 and speak_now.noul >= 0.5 else stay_silent
            return outcome(chosen.decision, chosen.reason, chosen)
        except (JevCallError, TimeoutError, ValueError, KeyError):
            detail.update(kind='jev', failed=True, error_type='jev_failed')
            return outcome(reason='jev_failed')
