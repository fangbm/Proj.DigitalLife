"""Optional versioned life subsystem; core schema 12 and legacy rows remain unchanged."""
from .models import utc_now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS life_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_states(device TEXT, run_kind TEXT, state TEXT NOT NULL, PRIMARY KEY(device,run_kind));
CREATE TABLE IF NOT EXISTS life_controls(device TEXT PRIMARY KEY, mode TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_cycles(task_run_id TEXT PRIMARY KEY, device TEXT NOT NULL, run_kind TEXT NOT NULL, context TEXT, decision TEXT, reflection TEXT, transition TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS life_goals(goal_id TEXT PRIMARY KEY, device TEXT NOT NULL, run_kind TEXT NOT NULL, candidate TEXT NOT NULL, status TEXT NOT NULL, source_run TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_memories(memory_id TEXT PRIMARY KEY, device TEXT NOT NULL, run_kind TEXT NOT NULL, layer TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL, importance REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, stale INTEGER NOT NULL DEFAULT 0, UNIQUE(device,run_kind,layer,digest));
CREATE INDEX IF NOT EXISTS life_memory_lookup ON life_memories(device,run_kind,layer,stale,updated_at);
CREATE TABLE IF NOT EXISTS life_memory_sources(memory_id TEXT, task_run_id TEXT, relation TEXT NOT NULL, PRIMARY KEY(memory_id,task_run_id,relation));
CREATE TABLE IF NOT EXISTS life_memory_links(from_memory TEXT, to_memory TEXT, relation TEXT NOT NULL, PRIMARY KEY(from_memory,to_memory,relation));
CREATE TABLE IF NOT EXISTS life_interests(device TEXT, run_kind TEXT, topic TEXT, weight REAL NOT NULL, evidence_count INTEGER NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(device,run_kind,topic));
CREATE TABLE IF NOT EXISTS life_requests(request_id TEXT PRIMARY KEY, device TEXT NOT NULL, run_kind TEXT NOT NULL, task_run_id TEXT NOT NULL, phase TEXT NOT NULL, requested_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL, details TEXT);
CREATE INDEX IF NOT EXISTS life_requests_time ON life_requests(device,run_kind,requested_at);
CREATE TABLE IF NOT EXISTS life_recovery(task_run_id TEXT PRIMARY KEY, classification TEXT NOT NULL, review TEXT NOT NULL, recovered_at TEXT NOT NULL);
'''


def enable_life(store):
    if store.readonly:
        raise ValueError('Cannot migrate a read-only Store')
    # executescript commits any prior transaction; only allowed at controller construction.
    try:
        store.conn.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
        version = store.conn.execute('SELECT MAX(version) FROM life_schema_migrations').fetchone()[0]
        if version is not None and version > 1:
            raise ValueError('Life schema is newer than this program')
        store.conn.execute('INSERT OR IGNORE INTO life_schema_migrations VALUES(1,?)', (utc_now(),))
        store.conn.execute('COMMIT')
    except BaseException:
        if store.conn.in_transaction:
            store.conn.execute('ROLLBACK')
        raise


def control(store, device, mode, reason):
    if mode not in {'running', 'paused', 'stopped'}:
        raise ValueError('Unknown control mode')
    store.conn.execute('INSERT INTO life_controls VALUES(?,?,?,?) ON CONFLICT(device) DO UPDATE SET mode=excluded.mode,reason=excluded.reason,updated_at=excluded.updated_at',
                       (device, mode, reason[:300], utc_now()))
    store.event(device, 'life_control', {'mode': mode, 'reason': reason, 'run_kind': 'autonomous'})


def control_mode(store, device):
    row = store.conn.execute('SELECT mode FROM life_controls WHERE device=?', (device,)).fetchone()
    return row[0] if row else 'running'
