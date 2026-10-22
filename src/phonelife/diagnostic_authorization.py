"""Non-issuing diagnostic authorization protocol; offline verifier is mock-only.

No runtime API mints approval. DPAPI is decoding-only, with separate entropy.
Local-account DPAPI cannot establish consent against code running as that owner.
"""
from __future__ import annotations
import ctypes
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from .diagnostic_evidence import DiagnosticDenied, canonical, sha, _windows_acl

PURPOSE = 'PHYSICAL_DIAGNOSTIC_CAPTURE'
ENTROPY = b'Proj.DigitalLife PHYSICAL_DIAGNOSTIC_CAPTURE v1'
OPERATIONS = frozenset({'CAPTURE_SCREEN_PNG', 'CAPTURE_UI_XML'})


def strict_json(raw):
    if not isinstance(raw, bytes) or len(raw) > 32768: raise DiagnosticDenied('PERMIT_SIZE')
    def unique(pairs):
        result = {}
        for k,v in pairs:
            if k in result: raise DiagnosticDenied('PERMIT_DUPLICATE_KEY')
            result[k] = v
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(value, dict): raise ValueError()
        return value
    except (ValueError, UnicodeError): raise DiagnosticDenied('PERMIT_INVALID_JSON') from None


def unprotect_diagnostic(blob):
    """Future operator artifact reader. Not wired to real capture in Prep."""
    if os.name != 'nt' or not isinstance(blob, bytes) or not 1 <= len(blob) <= 65536:
        raise DiagnosticDenied('DIAGNOSTIC_DPAPI_UNAVAILABLE')
    class Blob(ctypes.Structure):
        _fields_ = [('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
    data = ctypes.create_string_buffer(blob); entropy = ctypes.create_string_buffer(ENTROPY)
    source = Blob(len(blob),ctypes.cast(data,ctypes.POINTER(ctypes.c_ubyte)))
    ent = Blob(len(ENTROPY),ctypes.cast(entropy,ctypes.POINTER(ctypes.c_ubyte))); out = Blob()
    kernel = ctypes.WinDLL('kernel32'); kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source),None,ctypes.byref(ent),None,None,1,ctypes.byref(out)):
        raise DiagnosticDenied('DIAGNOSTIC_DPAPI_INVALID')
    try: return ctypes.string_at(out.data,out.size)
    finally: kernel.LocalFree(out.data)


class OfflinePermitVerifier:
    """Only verifies synthetic HMAC envelopes. No production authorization issuer."""
    def __init__(self, fixture_key):
        if not isinstance(fixture_key, bytes) or len(fixture_key) < 32: raise DiagnosticDenied('FIXTURE_KEY_REQUIRED')
        self.key = fixture_key

    def verify(self, blob):
        envelope = strict_json(blob)
        if set(envelope) != {'offline_fixture','payload','mac'} or envelope['offline_fixture'] is not True or not isinstance(envelope['mac'],str):
            raise DiagnosticDenied('FIXTURE_ENVELOPE_REQUIRED')
        if not hmac.compare_digest(envelope['mac'],hmac.new(self.key,canonical(envelope['payload']),hashlib.sha256).hexdigest()):
            raise DiagnosticDenied('PERMIT_TAMPERED')
        return envelope['payload']


def validate_permit(grant, *, identity_sha256, output_directory, now):
    fields = {'schema','purpose','identity_sha256','operations','page_scope','not_before','expires_at',
              'output_directory','nonce','operator_approved','operator_reference','reason','audit_id','category'}
    if not isinstance(grant,dict) or set(grant) != fields: raise DiagnosticDenied('PERMIT_SCHEMA')
    if grant['schema'] != 'diagnostic-approval/1' or grant['purpose'] != PURPOSE:
        raise DiagnosticDenied('PERMIT_PURPOSE')
    if grant['category'] != 'offline_fixture': raise DiagnosticDenied('PREP_REAL_APPROVAL_DISABLED')
    if grant['operator_approved'] is not True: raise DiagnosticDenied('OPERATOR_APPROVAL_REQUIRED')
    if grant['identity_sha256'] != identity_sha256: raise DiagnosticDenied('PERMIT_DEVICE_MISMATCH')
    if grant['page_scope'] != 'settings_home': raise DiagnosticDenied('PERMIT_SCOPE')
    for k in ('nonce','audit_id'):
        if not isinstance(grant[k],str) or not re.fullmatch(r'[0-9a-f]{32}',grant[k]): raise DiagnosticDenied('PERMIT_IDENTIFIER')
    for k in ('operator_reference','reason'):
        if not isinstance(grant[k],str) or not 1 <= len(grant[k].strip()) <= 200: raise DiagnosticDenied('PERMIT_REFERENCE')
    for k in ('not_before','expires_at'):
        if type(grant[k]) not in {int,float} or not math.isfinite(grant[k]): raise DiagnosticDenied('PERMIT_TIME')
    if not math.isfinite(now) or not grant['not_before'] <= now < grant['expires_at'] or not 0 < grant['expires_at']-grant['not_before'] <= 600:
        raise DiagnosticDenied('PERMIT_EXPIRED_OR_WINDOW')
    if not isinstance(grant['output_directory'],str) or grant['output_directory'] != str(Path(output_directory)):
        raise DiagnosticDenied('PERMIT_OUTPUT_MISMATCH')
    ops = grant['operations']
    if not isinstance(ops,dict) or not ops or not set(ops) <= OPERATIONS or any(type(v) is not int or not 1 <= v <= (2 if k == 'CAPTURE_SCREEN_PNG' else 1) for k,v in ops.items()):
        raise DiagnosticDenied('PERMIT_OPERATIONS')
    return grant


class DiagnosticLedger:
    """Dedicated private journal; consume durably BEFORE any capture dispatch.

    A started/unknown/failed request is spent. No automatic replay or recovery.
    No copies in life memory, TaskRun, budget or formal session tables.
    """
    def __init__(self, evidence):
        evidence.check(); self.path = evidence.path / 'diagnostic.sqlite3'
        if self.path.exists() and (self.path.is_symlink() or self.path.stat().st_nlink != 1):
            raise DiagnosticDenied('LEDGER_PATH_INVALID')
        if not self.path.exists():
            fd = os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd)
        if os.name == 'nt': _windows_acl(self.path)
        self.conn = sqlite3.connect(self.path, timeout=.25, isolation_level=None)
        self.conn.execute('PRAGMA synchronous=FULL')
        self.conn.executescript('''CREATE TABLE IF NOT EXISTS permits(nonce TEXT PRIMARY KEY, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS captures(request_id TEXT PRIMARY KEY, nonce TEXT NOT NULL, operation TEXT NOT NULL,
            status TEXT NOT NULL, record TEXT NOT NULL);''')

    def consume(self, grant, request_id, operation, record):
        try:
            self.conn.execute('BEGIN IMMEDIATE')
            row = self.conn.execute('SELECT digest FROM permits WHERE nonce=?',(grant['nonce'],)).fetchone()
            digest = sha(canonical(grant))
            if row and row[0] != digest: raise DiagnosticDenied('PERMIT_NONCE_CONFLICT')
            self.conn.execute('INSERT OR IGNORE INTO permits VALUES(?,?)',(grant['nonce'],digest))
            if operation not in grant['operations']: raise DiagnosticDenied('PERMIT_TYPE_NOT_ALLOWED')
            used = self.conn.execute('SELECT COUNT(*) FROM captures WHERE nonce=? AND operation=?',(grant['nonce'],operation)).fetchone()[0]
            if used >= grant['operations'][operation]: raise DiagnosticDenied('PERMIT_EXHAUSTED')
            self.conn.execute('INSERT INTO captures VALUES(?,?,?,?,?)',(request_id,grant['nonce'],operation,'started',canonical(record).decode()))
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction: self.conn.execute('ROLLBACK')
            raise

    def finish(self, request_id, status, record):
        self.conn.execute('UPDATE captures SET status=?,record=? WHERE request_id=?',(status,canonical(record).decode(),request_id))

    def close(self): self.conn.close()
