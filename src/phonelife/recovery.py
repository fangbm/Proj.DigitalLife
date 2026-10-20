"""Journal interrupted work under an acquired device lease; never re-dispatch."""
import json
import time
from .models import utc_now


def recover_interrupted(store, device, exclude_run):
    rows = store.conn.execute("SELECT * FROM task_runs WHERE device=? AND status='running' AND task_run_id!=?", (device, exclude_run)).fetchall()
    recovered = []
    for row in rows:
        run_id = row['task_run_id']
        attempts = store.task(run_id)['attempts']
        uncertain = any(a['payload'].get('execution_status') == 'uncertain_after_transport_error' for a in attempts)
        executed = any(a['payload'].get('execution_status') == 'executed' for a in attempts)
        verified = any(json.loads(event[0]).get('task_run_id') == run_id for event in
                       store.conn.execute("SELECT payload FROM events WHERE device=? AND event_type='verification' ORDER BY id DESC LIMIT 200", (device,)))
        classification = ('action_outcome_unknown' if uncertain else 'verification_finished_not_finalized' if verified
                          else 'executed_not_verified' if executed else 'not_dispatched')
        status = 'needs_review' if uncertain or executed or verified else 'interrupted'
        review = {'original_status': row['status'], 'status': status, 'reason_code': 'PROCESS_INTERRUPTED',
                  'classification': classification, 'old_actions_replayed': 0,
                  'required_next_step': 'fresh_observation_then_new_model_decision', 'run_kind': row['run_kind']}
        try:
            store.conn.execute('BEGIN IMMEDIATE')
            store.conn.execute('UPDATE task_runs SET status=?,ended_wall_utc=?,result=? WHERE task_run_id=? AND status=\'running\'',
                               (status, utc_now(), store._json(review), run_id))
            store.conn.execute('INSERT OR IGNORE INTO life_recovery VALUES(?,?,?,?)', (run_id, classification, store._json(review), utc_now()))
            store.conn.execute("UPDATE life_goals SET status='needs_review',updated_at=? WHERE source_run=? AND status='active'", (utc_now(), run_id))
            store.conn.execute("UPDATE life_requests SET status='interrupted_outcome_unknown' WHERE task_run_id=? AND status='reserved'", (run_id,))
            store.event(device, 'interrupted_recovery', {'task_run_id': run_id, **review})
            store.conn.execute('COMMIT')
        except BaseException:
            if store.conn.in_transaction:
                store.conn.execute('ROLLBACK')
            raise
        recovered.append({'task_run_id': run_id, **review})
    return recovered
