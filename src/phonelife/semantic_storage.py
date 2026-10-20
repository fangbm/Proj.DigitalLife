"""Additive feature schema; core schema 12 and all legacy rows remain untouched."""
import hashlib
import json
import uuid
from contextvars import ContextVar
from pathlib import Path
from .models import utc_now

COLLECTION_SINK=ContextVar('phonelife_semantic_collection_sink',default=None)


def journal_collection(semantic):
    sink=COLLECTION_SINK.get()
    if sink is not None:sink(semantic)


def record_additional_semantics(store,leases,name,owner,run_id,semantic,evidence_dir,lease_seconds):
    """Repeated collections of an already recorded PNG, including independent verification.

    The first collection is recorded by PhoneLife._observe. Additional samples are immutable
    events/private captures, never updates to its original semantic row. Sink errors propagate
    outside backend error handling, so audit failure cannot silently become trusted evidence.
    """
    row=store.conn.execute('SELECT task_run_id,screenshot_sha256 FROM semantic_observations WHERE observation_id=?',
                           (semantic.observation_id,)).fetchone()
    if row is None:return
    if row[0]!=run_id or row[1]!=semantic.screenshot_sha256:
        raise ValueError('SEMANTIC_SAMPLE_SOURCE_MISMATCH')
    leases.renew(name,owner,lease_seconds)
    sample_id=str(uuid.uuid4());path=None;frames=[]
    raw=json.dumps(semantic.raw,ensure_ascii=False,sort_keys=True).encode()
    digest=hashlib.sha256(raw).hexdigest()
    if evidence_dir:
        prefix=run_id+'-'+semantic.observation_id+'-additional-'+sample_id
        path=Path(evidence_dir)/(prefix+'-PRIVATE-ui.json')
        with path.open('xb') as stream:stream.write(raw)
        path.chmod(0o600)
        for index,frame in enumerate(semantic.private_frames):
            frame_path=Path(evidence_dir)/(prefix+f'-sample-{index}-PRIVATE.png')
            with frame_path.open('xb') as stream:stream.write(frame.png)
            frame_path.chmod(0o600)
            frames.append({'path':str(frame_path),'sha256':frame.sha256,'index':index})
    store.event(name,'additional_semantic_collection',{'task_run_id':run_id,'sample_id':sample_id,
        'observation_id':semantic.observation_id,'screenshot_sha256':semantic.screenshot_sha256,
        'assessment':semantic.audit(),'raw_sha256':digest,'private_path':str(path) if path else None,'private_frames':frames})


def enable_semantics(store):
    if store.readonly:
        raise ValueError('No migrations during read-only replay')
    try:
        store.conn.executescript('''BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS semantic_schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS semantic_observations(
                observation_id TEXT PRIMARY KEY,task_run_id TEXT NOT NULL,screenshot_sha256 TEXT NOT NULL,
                provider TEXT NOT NULL,evidence_quality TEXT NOT NULL,payload TEXT NOT NULL,
                raw_sha256 TEXT,private_path TEXT,
                FOREIGN KEY(observation_id) REFERENCES observations(observation_id),
                FOREIGN KEY(task_run_id) REFERENCES task_runs(task_run_id));
            CREATE INDEX IF NOT EXISTS semantic_run ON semantic_observations(task_run_id);
        ''')
        version=store.conn.execute('SELECT MAX(version) FROM semantic_schema_migrations').fetchone()[0]
        if version is not None and version>1:
            raise ValueError('Semantic schema newer than this program')
        store.conn.execute('INSERT OR IGNORE INTO semantic_schema_migrations VALUES(1,?)',(utc_now(),))
        store.conn.execute('COMMIT')
    except BaseException:
        if store.conn.in_transaction: store.conn.execute('ROLLBACK')
        raise


def record_semantics(store, run_id, semantic, evidence_dir):
    raw=json.dumps(semantic.raw,ensure_ascii=False,sort_keys=True).encode()
    sha=hashlib.sha256(raw).hexdigest(); path=None
    if evidence_dir:
        path=Path(evidence_dir)/(run_id+'-'+semantic.observation_id+'-PRIVATE-ui.json')
        with path.open('xb') as output: output.write(raw)
        path.chmod(0o600)
        for index,frame in enumerate(semantic.private_frames):
            frame_path=Path(evidence_dir)/(run_id+'-'+semantic.observation_id+f'-sample-{index}-PRIVATE.png')
            with frame_path.open('xb') as output: output.write(frame.png)
            frame_path.chmod(0o600)
    store.conn.execute('INSERT INTO semantic_observations VALUES(?,?,?,?,?,?,?,?)',
        (semantic.observation_id,run_id,semantic.screenshot_sha256,semantic.provider,semantic.evidence_quality,
         store._json(semantic.audit()),sha,str(path) if path else None))


async def prepare_semantics(store, leases, name, phone):
    import asyncio, uuid
    from .semantic import ShellSnapshotBackend
    run_id=str(uuid.uuid4())
    if not leases.acquire(name,run_id,60):
        return {'status':'busy','reason_code':'DEVICE_BUSY'}
    started=False
    try:
        store.start_task(run_id,name,'Deploy read-only semantic shell observer',
            {'mode':'semantic_setup','timeout_seconds':20},run_kind='manual_targeted')
        started=True
        setup=await asyncio.wait_for(ShellSnapshotBackend().prepare(phone),20)
        result={'status':'rested','reason_code':'SEMANTIC_BACKEND_PREPARED','task_run_id':run_id,
                'summary':'Read-only shell backend deployed and hash checked','steps':0,'setup':setup}
        store.event(name,'semantic_setup',result)
        return result
    except BaseException as error:
        result={'status':'interrupted','reason_code':'SEMANTIC_SETUP_FAILED','task_run_id':run_id,
                'summary':type(error).__name__,'steps':0}
        raise
    finally:
        try:
            if started: store.finish_task(run_id,result,store.next_wake(name,run_kind='manual_targeted'),result['summary'])
        finally: leases.release(name,run_id)
