"""Opt-in smoke-test accounting; every forwarded attempt consumes a budget."""
import threading


class ModelBudget:
    def __init__(self, *, agent=120, scenario=50, semantic=64, safety=16, fallback=16):
        if any(type(x) is not int or x < 1 for x in (agent, scenario, semantic, safety, fallback)):
            raise ValueError('Budgets must be positive integers')
        self.limits = {'agent': agent, 'semantic': semantic, 'safety': safety, 'fallback': fallback}
        self.counts = dict.fromkeys(self.limits, 0)
        self.scenario_limit, self.scenario_count = scenario, 0
        self.stopped_reason = None
        self.lock = threading.Lock()

    def consume(self, kind):
        with self.lock:
            if self.stopped_reason:
                raise ValueError(self.stopped_reason)
            if kind not in self.limits or self.counts[kind] >= self.limits[kind]:
                raise ValueError(f'{kind} request budget exhausted')
            if kind == 'agent' and self.scenario_count >= self.scenario_limit:
                raise ValueError('scenario request budget exhausted')
            self.counts[kind] += 1
            if kind == 'agent':
                self.scenario_count += 1

    def new_scenario(self):
        with self.lock:
            self.scenario_count = 0

    def stop(self, reason):
        with self.lock:
            self.stopped_reason = reason
