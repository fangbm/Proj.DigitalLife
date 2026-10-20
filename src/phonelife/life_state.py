"""Explainable bounded state, updated from measured events rather than model numbers."""
from dataclasses import asdict, dataclass, field


def clamp(value):
    return round(min(1., max(0., value)), 6)


@dataclass
class LifeState:
    curiosity: float = .65
    energy: float = .8
    fatigue: float = .1
    boredom: float = .0
    confidence: float = .5
    consecutive_failures: int = 0
    cycles: int = 0
    last_update: float = 0.

    def wire(self):
        return asdict(self)

    def evolve(self, *, now, status, actions, api_calls, novelty, repeated, resting=False):
        before = self.wire()
        elapsed = max(0, now - self.last_update) if self.last_update else 0
        recovery = min(.12, elapsed / 3600 * .15)  # Actual elapsed time, never promised sleep.
        cost = min(.12, actions * .018 + api_calls * .002)
        self.energy = clamp(self.energy + recovery - cost)
        self.fatigue = clamp(self.fatigue + cost - recovery)
        self.boredom = clamp(self.boredom + (.08 if repeated else -.04 if novelty else 0))
        self.curiosity = clamp(self.curiosity + (.025 if novelty else -.025 if repeated else 0))
        failure = status in {'failed', 'blocked', 'step_limit', 'interrupted', 'needs_review'}
        self.consecutive_failures = self.consecutive_failures + 1 if failure else 0 if status == 'succeeded' else self.consecutive_failures
        self.confidence = clamp(self.confidence + (.04 if status == 'succeeded' else -.06 if failure else -.01 if status == 'uncertain' else 0))
        self.cycles += 1
        self.last_update = now
        return {'before': before, 'after': self.wire(), 'causes': {
            'status': status, 'confirmed_actions': actions, 'metered_api_requests': api_calls,
            'new_content_observed': bool(novelty), 'repeated_goal': bool(repeated),
            'actual_elapsed_seconds': elapsed, 'energy_recovery': recovery, 'activity_cost': cost,
            'rest_decision': resting}}
