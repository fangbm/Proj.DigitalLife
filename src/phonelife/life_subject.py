"""Explicit, additive life identity migration. Never edits historical device origins.

Only called on an operator-selected copy. No discovery/guessing of life subjects.
The legacy device columns of state/interest/schedule also support subject:<id>
namespaces; observation/task/action rows always retain their execution device.
"""
from dataclasses import dataclass
import hashlib
import json
import re
from .models import utc_now

SCHEMA='''
CREATE TABLE IF NOT EXISTS life_subjects(life_subject_id TEXT PRIMARY KEY,legacy_source TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_device_bindings(execution_device_id TEXT PRIMARY KEY,life_subject_id TEXT NOT NULL,device_profile_id TEXT NOT NULL,device_session_id TEXT NOT NULL,source_kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_run_bindings(task_run_id TEXT PRIMARY KEY,life_subject_id TEXT NOT NULL,execution_device_id TEXT NOT NULL,device_profile_id TEXT NOT NULL,device_session_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_device_sessions(device_session_id TEXT PRIMARY KEY,execution_device_id TEXT NOT NULL,life_subject_id TEXT NOT NULL,device_profile_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_subject_migrations(life_subject_id TEXT PRIMARY KEY,source_sha256 TEXT NOT NULL,source_device TEXT NOT NULL,completed_at TEXT NOT NULL);
'''

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',value):raise ValueError('INVALID_LIFE_BINDING_ID')
    return value

@dataclass(frozen=True)
class LifeBinding:
    life_subject_id:str
    execution_device_id:str
    device_profile_id:str
    device_session_id:str
    def __post_init__(self):
        for v in self.__dict__.values():identifier(v)
    @property
    def namespace(self):return 'subject:'+self.life_subject_id
    def verify(self,conn):
        row=conn.execute('SELECT life_subject_id,device_profile_id FROM life_device_bindings WHERE execution_device_id=?',(self.execution_device_id,)).fetchone()
        session=conn.execute('SELECT execution_device_id,life_subject_id,device_profile_id FROM life_device_sessions WHERE device_session_id=?',(self.device_session_id,)).fetchone()
        if not row or tuple(row)!=(self.life_subject_id,self.device_profile_id) or not session or tuple(session)!=(self.execution_device_id,self.life_subject_id,self.device_profile_id):raise ValueError('LIFE_SUBJECT_BINDING_MISMATCH')
        if not conn.execute('SELECT 1 FROM life_subject_migrations WHERE life_subject_id=?',(self.life_subject_id,)).fetchone():raise ValueError('LIFE_SUBJECT_MIGRATION_INCOMPLETE')
    def devices(self,conn):
        self.verify(conn)
        return tuple(r[0] for r in conn.execute('SELECT execution_device_id FROM life_device_bindings WHERE life_subject_id=? ORDER BY execution_device_id',(self.life_subject_id,)))
    def bind_run(self,store,run_id):
        self.verify(store.conn)
        row=store.conn.execute('SELECT device,run_kind FROM task_runs WHERE task_run_id=?',(run_id,)).fetchone()
        if not row or tuple(row)!=(self.execution_device_id,'autonomous'):raise ValueError('LIFE_RUN_SOURCE_MISMATCH')
        values=(run_id,self.life_subject_id,self.execution_device_id,self.device_profile_id,self.device_session_id)
        store.conn.execute('INSERT OR IGNORE INTO life_run_bindings VALUES(?,?,?,?,?)',values)
        if tuple(store.conn.execute('SELECT * FROM life_run_bindings WHERE task_run_id=?',(run_id,)).fetchone())!=values:raise ValueError('LIFE_RUN_BINDING_CONFLICT')

def migrate_subject(store,binding,*,source_device,source_sha256,fault=None):
    """Atomic and idempotent. Caller supplies a consistent-copy SHA and exact source.

    Additive provenance and subject state copies only. Never migrate the production
    DB implicitly at controller startup, and never import manual/benchmark/mock.
    """
    identifier(source_device)
    if not re.fullmatch('[0-9a-f]{64}',source_sha256):raise ValueError('CONSISTENT_SOURCE_SHA_REQUIRED')
    if store.readonly:raise ValueError('COPY_MIGRATION_REQUIRES_WRITABLE_COPY')
    c=store.conn
    if c.in_transaction:raise ValueError('SUBJECT_MIGRATION_REQUIRES_OWN_TRANSACTION')
    # The optional subsystem must already exist; refuses a guessed/empty source.
    if not c.execute("SELECT 1 FROM task_runs WHERE device=? AND run_kind='autonomous' LIMIT 1",(source_device,)).fetchone():raise ValueError('EXPLICIT_AUTONOMOUS_SOURCE_REQUIRED')
    try:
        c.executescript('BEGIN IMMEDIATE;'+SCHEMA)
        old=c.execute('SELECT source_sha256,source_device FROM life_subject_migrations WHERE life_subject_id=?',(binding.life_subject_id,)).fetchone()
        if old and tuple(old)!=(source_sha256,source_device):raise ValueError('SUBJECT_SOURCE_CONFLICT')
        c.execute('INSERT OR IGNORE INTO life_subjects VALUES(?,?,?)',(binding.life_subject_id,source_device,utc_now()))
        if c.execute('SELECT life_subject_id FROM life_subjects WHERE legacy_source=?',(source_device,)).fetchone()[0]!=binding.life_subject_id:raise ValueError('AMBIGUOUS_LIFE_SUBJECT')
        def insert_device(device,profile,session,source):
            values=(device,binding.life_subject_id,profile,session,source)
            c.execute('INSERT OR IGNORE INTO life_device_bindings VALUES(?,?,?,?,?)',values)
            recorded=tuple(c.execute('SELECT * FROM life_device_bindings WHERE execution_device_id=?',(device,)).fetchone())
            if recorded[:3]!=values[:3] or recorded[4]!=source:raise ValueError('DEVICE_SUBJECT_BINDING_CONFLICT')
        insert_device(source_device,'legacy-explicit-source','legacy','legacy')
        insert_device(binding.execution_device_id,binding.device_profile_id,binding.device_session_id,'explicit_device_binding')
        session=(binding.device_session_id,binding.execution_device_id,binding.life_subject_id,binding.device_profile_id)
        c.execute('INSERT OR IGNORE INTO life_device_sessions VALUES(?,?,?,?)',session)
        if tuple(c.execute('SELECT * FROM life_device_sessions WHERE device_session_id=?',(binding.device_session_id,)).fetchone())!=session:raise ValueError('DEVICE_SESSION_BINDING_CONFLICT')
        if not old:
            c.execute("INSERT INTO life_run_bindings SELECT task_run_id,?,device,'legacy-explicit-source','legacy' FROM task_runs WHERE device=? AND run_kind='autonomous'",(binding.life_subject_id,source_device))
            c.execute("INSERT INTO life_states SELECT ?,run_kind,state FROM life_states WHERE device=? AND run_kind='autonomous'",(binding.namespace,source_device))
            c.execute("INSERT INTO life_interests SELECT ?,run_kind,topic,weight,evidence_count,updated_at FROM life_interests WHERE device=? AND run_kind='autonomous'",(binding.namespace,source_device))
            c.execute('INSERT INTO schedule SELECT ?,next_wake_at,last_result FROM schedule WHERE device=?',(binding.namespace,source_device))
            if fault:fault('before_commit')
            c.execute('INSERT INTO life_subject_migrations VALUES(?,?,?,?)',(binding.life_subject_id,source_sha256,source_device,utc_now()))
        c.execute('COMMIT');binding.verify(c)
    except BaseException:
        if c.in_transaction:c.execute('ROLLBACK')
        raise
    return {'life_subject_id':binding.life_subject_id,'source_sha256':source_sha256,'source_device':source_device,
        'execution_device_id':binding.execution_device_id,'historical_device_origins_rewritten':0,'old_actions_replayed':0}

def attach_subject(store,binding):
    binding.verify(store.conn)
    store.life_binding=binding

def scope(store,device,run_kind):
    binding=getattr(store,'life_binding',None)
    if binding and run_kind=='autonomous':
        if device!=binding.execution_device_id:raise ValueError('EXECUTION_DEVICE_BINDING_MISMATCH')
        return binding.devices(store.conn)
    return (device,)

def predicate(devices,column='device'):
    if column not in {'device','e.device'}:raise ValueError('INVALID_SCOPE_COLUMN')
    return column+' IN ('+','.join('?' for _ in devices)+')'
