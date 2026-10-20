"""Strict, bounded contracts for model choices, never device permissions."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from .models import utc_now


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('Unexpected or missing life-contract fields')


def text(value, limit=600, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError('Invalid bounded text')
    return value.strip()


def unit(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Expected a finite number in 0..1')
    return float(value)


def verification_spec(value, depth=0):
    if not isinstance(value, dict) or depth > 1:
        raise ValueError('Invalid verification specification')
    kind = value.get('kind')
    keys = {'foreground': {'kind', 'package'}, 'page': {'kind', 'package', 'title'},
            'controls': {'kind', 'package', 'labels'}, 'visual': {'kind', 'question'},
            'observe': {'kind'}, 'readonly_fact': {'kind', 'facts'}, 'about_phone': {'kind'},
            'all': {'kind', 'checks'}, 'new_scroll_content': {'kind'}}
    if kind not in keys:
        raise ValueError('Unknown verification rule')
    exact(value, keys[kind])
    if 'package' in value:
        text(value['package'], 200)
    if kind == 'page':
        text(value['title'], 80)
    if kind == 'visual':
        text(value['question'], 300)
    if kind in {'controls', 'readonly_fact'}:
        items = value['labels' if kind == 'controls' else 'facts']
        if not isinstance(items, list) or not 1 <= len(items) <= 5:
            raise ValueError('Bounded nonempty verification list required')
        for item in items:
            text(item, 80)
        if kind == 'readonly_fact' and not set(items) <= {'android_version', 'model', 'battery_percent', 'storage_free_kb'}:
            raise ValueError('Unsupported read-only fact')
    if kind == 'all':
        if depth or not isinstance(value['checks'], list) or not 2 <= len(value['checks']) <= 3:
            raise ValueError('Composite verification limited to three leaf rules')
        for check in value['checks']:
            verification_spec(check, depth + 1)
    return value


@dataclass(frozen=True)
class GoalCandidate:
    goal_id: str
    description: str
    motivation: str
    expected_value: float
    novelty: float
    estimated_cost: int
    risk_class: str
    continuation_of: str | None
    created_at: str
    verification: dict

    @classmethod
    def from_dict(cls, value):
        exact(value, cls.__dataclass_fields__)
        for key, limit in [('goal_id', 80), ('description', 500), ('motivation', 500), ('created_at', 50)]:
            text(value[key], limit)
        from datetime import datetime
        if datetime.fromisoformat(value['created_at'].replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('created_at needs a timezone')
        for key in ['expected_value', 'novelty']:
            unit(value[key])
        if type(value['estimated_cost']) is not int or not 0 <= value['estimated_cost'] <= 100:
            raise ValueError('estimated_cost is an action count in 0..100')
        if value['risk_class'] not in {'low', 'medium', 'high'}:
            raise ValueError('Unknown risk class')
        if value['continuation_of'] is not None:
            text(value['continuation_of'], 80)
        verification_spec(value['verification'])
        return cls(**value)


@dataclass(frozen=True)
class LifeDecision:
    kind: str
    candidates: list[GoalCandidate]
    selected_goal_id: str | None
    reason: str
    wake_after_seconds: int

    @classmethod
    def from_dict(cls, value):
        exact(value, cls.__dataclass_fields__)
        if value['kind'] not in {'explore', 'continue', 'switch_goal', 'reflect', 'rest'}:
            raise ValueError('Unknown life decision')
        if not isinstance(value['candidates'], list) or len(value['candidates']) > 5:
            raise ValueError('At most five goal candidates')
        candidates = [GoalCandidate.from_dict(c) for c in value['candidates']]
        ids = [c.goal_id for c in candidates]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate goal id')
        active = value['kind'] in {'explore', 'continue', 'switch_goal'}
        if (active and value['selected_goal_id'] not in ids) or (not active and value['selected_goal_id'] is not None):
            raise ValueError('Decision must select exactly one candidate, or none for rest/reflect')
        if type(value['wake_after_seconds']) is not int or not 1 <= value['wake_after_seconds'] <= 86400:
            raise ValueError('Invalid wake interval')
        text(value['reason'], 700)
        return cls(value['kind'], candidates, value['selected_goal_id'], value['reason'], value['wake_after_seconds'])

    @property
    def selected(self):
        return next((g for g in self.candidates if g.goal_id == self.selected_goal_id), None)


@dataclass(frozen=True)
class Reflection:
    attempted: str
    subjective_outcome: str
    discoveries: list[str]
    topics: list[str]
    worth_continuing: bool
    next_adjustment: str

    @classmethod
    def from_dict(cls, value):
        exact(value, cls.__dataclass_fields__)
        for key in ['attempted', 'subjective_outcome', 'next_adjustment']:
            text(value[key], 600)
        for key, count, limit in [('discoveries', 5, 300), ('topics', 5, 60)]:
            if not isinstance(value[key], list) or len(value[key]) > count:
                raise ValueError('Reflection list too large')
            for item in value[key]:
                text(item, limit)
        if type(value['worth_continuing']) is not bool:
            raise ValueError('worth_continuing must be bool')
        return cls(**value)


@dataclass(frozen=True)
class LifeContext:
    now: str
    uptime_seconds: float
    device: dict
    screen: dict
    recent_episodes: list[dict]
    relevant_memories: list[dict]
    interests: list[dict]
    recent_failures: list[dict]
    unfinished_goals: list[dict]
    internal_state: dict
    budgets: dict
    safety_constraints: dict
    semantic_environment: dict = field(default_factory=dict)

    def wire(self):
        return asdict(self)
