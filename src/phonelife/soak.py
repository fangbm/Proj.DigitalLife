"""Bounded or resident autonomous runs, persistent control and read-only metrics."""
import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from . import __version__
from .audit import EvidenceSink
from .life_storage import control_mode
from .models import utc_now


def statistics(store, device, run_kind='autonomous', lease_store=None):
    available = store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='life_cycles'").fetchone()
    if not available:
        return {'life_enabled': False, 'device': device}
    rows = store.conn.execute('SELECT t.status,c.decision,c.summary FROM life_cycles c JOIN task_runs t USING(task_run_id) WHERE c.device=? AND c.run_kind=?', (device,run_kind)).fetchall()
    statuses, decisions = {}, {}
    for row in rows:
        statuses[row[0]] = statuses.get(row[0], 0) + 1
        decision = json.loads(row[1])['kind'] if row[1] else 'unavailable'
        decisions[decision] = decisions.get(decision, 0) + 1
    requests = [dict(r) for r in store.conn.execute('SELECT phase,status,COUNT(*) AS requests FROM life_requests WHERE device=? AND run_kind=? GROUP BY phase,status', (device,run_kind))]
    return {'life_enabled': True, 'device': device, 'control': control_mode(store, device),
            'cycles': len(rows), 'statuses': statuses, 'decisions': decisions, 'requests': requests,
            'pending_review': store.conn.execute("SELECT COUNT(*) FROM task_runs WHERE device=? AND status='needs_review'", (device,)).fetchone()[0],
            'run_kind': run_kind, 'next_wake_at': store.next_wake(device,run_kind=run_kind),
            'lease': [dict(r) for r in (lease_store or store).conn.execute('SELECT * FROM lease WHERE device=?', (device,))],
            'state': [json.loads(r[0]) for r in store.conn.execute("SELECT state FROM life_states WHERE device=? AND run_kind=?", (device,run_kind))],
            'interests': [dict(r) for r in store.conn.execute("SELECT topic,weight,evidence_count FROM life_interests WHERE device=? AND run_kind=? ORDER BY weight DESC LIMIT 30", (device,run_kind))]}


async def run_life(life, *, cycles=None, hours=None, output=None, force=False):
    started = time.monotonic()
    deadline = started + hours * 3600 if hours is not None else float('inf')
    session = None
    if output:
        session = Path(output) / ('session-' + str(uuid.uuid4()))
        session.mkdir(parents=True, exist_ok=False)
        if life.phone.kind == 'emulator' and life.evidence.directory is None:
            life.evidence = EvidenceSink(session / 'screens', 'emulator')
        root = Path(__file__).parent
        metadata = {'version': __version__, 'started_wall_utc': utc_now(), 'requested_hours': hours,
                    'requested_cycles': cycles, 'no_user_gui_goal': True, 'force_first_wake': force,
                    'limits': asdict(life.autonomy.limits), 'device': life.name,
                    'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')}}
        if life.semantic_provider:
            metadata['capability_registry']=life.capabilities.wire()
            asset=root/'assets'/'ui-snapshot.jar'
            metadata['semantic_backend_asset_sha256']=hashlib.sha256(asset.read_bytes()).hexdigest()
        (session / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    startup = await asyncio.wait_for(life.autonomy.review_startup(life),timeout=max(.001,deadline-time.monotonic()))
    if session:
        (session/'startup-review.json').write_text(json.dumps(startup,ensure_ascii=False,indent=2),encoding='utf-8')
    completed = 0
    exported = set()
    def export(run_id):
        if not session or run_id in exported:
            return
        task = life.store.task(run_id)
        cycle = dict(life.store.conn.execute('SELECT * FROM life_cycles WHERE task_run_id=?', (run_id,)).fetchone())
        for key in ('context','decision','reflection','transition','summary'):
            cycle[key] = json.loads(cycle[key]) if cycle[key] else None
        with (session / 'cycles.jsonl').open('a', encoding='utf-8') as stream:
            events = [dict(row) for row in life.store.conn.execute('SELECT e.* FROM events e JOIN event_sources s ON s.event_id=e.id WHERE s.task_run_id=? ORDER BY e.id',(run_id,))]
            for event in events:
                event['payload'] = json.loads(event['payload'])
            stream.write(json.dumps({'task': task, 'life': cycle, 'events': events}, ensure_ascii=False) + '\n')
        exported.add(run_id)
    while time.monotonic() < deadline and (cycles is None or completed < cycles):
        mode = control_mode(life.store, life.name)
        if mode == 'stopped':
            break
        due = life.store.next_wake(life.name, run_kind=life.run_kind) - time.time()
        if mode == 'paused' or (due > 0 and not (force and completed == 0)):
            await asyncio.sleep(min(1., max(.01, deadline-time.monotonic())))
            continue
        try:
            result = await asyncio.wait_for(life.run_once(force=force and completed == 0), timeout=max(.001, deadline-time.monotonic()))
        except TimeoutError:
            if getattr(life.autonomy,'last_run_id',None):
                export(life.autonomy.last_run_id)
            break  # PhoneLife has journalled cancellation and released its lease.
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if result.get('task_run_id'):
            completed += 1
            if session:
                export(result['task_run_id'])
        elif result['status'] == 'busy':
            await asyncio.sleep(min(1., max(.01, deadline-time.monotonic())))
    summary = {'session': str(session) if session else None, 'completed_cycles_this_session': completed,
               'elapsed_monotonic_seconds': time.monotonic()-started, 'stats': statistics(life.store, life.name,life.run_kind,life.leases),
               'scope': 'Measured session duration only; no unexecuted 24h acceptance claim'}
    if session:
        (session / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary
