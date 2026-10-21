"""Durable request reservations. Failed/cancelled requests consume quota too."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from .audit import redact
from .llm import APIError, ModelOutputError, parse_object


@dataclass(frozen=True)
class LifeLimits:
    max_actions: int = 12
    hourly_requests: int = 120
    daily_requests: int = 500
    consecutive_failures: int = 3
    min_rest_seconds: int = 30
    task_timeout_seconds: int = 300
    max_goal_requests: int = 32

    def __post_init__(self):
        bounds = {'max_actions': (1, 50), 'hourly_requests': (1, 10000), 'daily_requests': (1, 50000),
                  'consecutive_failures': (1, 20), 'min_rest_seconds': (1, 86400),
                  'task_timeout_seconds': (1, 600), 'max_goal_requests': (1, 200)}
        for key, (low, high) in bounds.items():
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Invalid resource limit: ' + key)


class BudgetExceeded(APIError):
    def __init__(self, until, scope):
        self.until = until
        super().__init__('API_BUDGET_EXHAUSTED', 'Request budget exhausted: ' + scope)


class RequestBudget:
    def __init__(self, store, device, limits, run_kind='autonomous'):
        self.store, self.device, self.limits, self.run_kind = store, device, limits, run_kind
        self.run_id = ''
        from .life_subject import scope,predicate
        self.devices=scope(store,device,run_kind);self.scope=predicate(self.devices)

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        conn = self.store.conn
        counts = {name: conn.execute(f'SELECT COUNT(*) FROM life_requests WHERE {self.scope} AND run_kind=? AND requested_at>?',
                     (*self.devices, self.run_kind, now - seconds)).fetchone()[0]
                  for name, seconds in [('hour_used', 3600), ('day_used', 86400)]}
        counts['goal_used'] = conn.execute('SELECT COUNT(*) FROM life_requests WHERE task_run_id=?', (self.run_id,)).fetchone()[0]
        return {**asdict(self.limits), **counts}

    def reserve(self, phase):
        if not self.run_id:
            raise RuntimeError('API request outside a bound life cycle')
        conn = self.store.conn
        now = time.time()
        try:
            conn.execute('BEGIN IMMEDIATE')
            for duration, limit, scope in [(3600, self.limits.hourly_requests, 'hour'), (86400, self.limits.daily_requests, 'day')]:
                rows = conn.execute(f'SELECT requested_at FROM life_requests WHERE {self.scope} AND run_kind=? AND requested_at>? ORDER BY requested_at',
                                    (*self.devices, self.run_kind, now-duration)).fetchall()
                if len(rows) >= limit:
                    raise BudgetExceeded(rows[len(rows)-limit][0] + duration + 1, scope)
            if conn.execute('SELECT COUNT(*) FROM life_requests WHERE task_run_id=?', (self.run_id,)).fetchone()[0] >= self.limits.max_goal_requests:
                raise BudgetExceeded(now + 600, 'goal')
            request_id = str(uuid.uuid4())
            conn.execute('INSERT INTO life_requests(request_id,device,run_kind,task_run_id,phase,requested_at,status) VALUES(?,?,?,?,?,?,?)',
                         (request_id, self.device, self.run_kind, self.run_id, phase, now, 'reserved'))
            conn.execute('COMMIT')
            return request_id
        except BaseException:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            raise

    def finish(self, request_id, status, details):
        self.store.conn.execute('UPDATE life_requests SET status=?,details=?,finished_at=? WHERE request_id=?',
            (status, self.store._json(details), time.time(), request_id))


class MeteredAPI:
    def __init__(self, api, budget, phase):
        self.api, self.budget, self.phase = api, budget, phase
        self.provider = api.provider

    async def generate(self, *args, **kwargs):
        if not self.budget.run_id:
            # Shared executor's explicit manual/benchmark path remains outside life provenance.
            return await self.api.generate(*args, **kwargs)
        request_id = self.budget.reserve(self.phase)
        try:
            response = await self.api.generate(*args, **kwargs)
        except BaseException as error:
            self.budget.finish(request_id, 'failed_or_cancelled', {'code': getattr(error, 'code', type(error).__name__)})
            raise
        self.budget.finish(request_id, 'received', {'provider': response.provider, 'model': response.model,
                                                  'api_request_id': response.api_request_id})
        return response

    async def ask(self, *args, **kwargs):
        response = await self.generate(*args, **kwargs)
        if response.finish_reason != 'stop':
            raise ModelOutputError('Incomplete response')
        return parse_object(response.content, allow_fences=False)
