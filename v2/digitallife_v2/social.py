"""M4 scopes, source-bound conversations and a durable one-attempt outbox."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit
from .safety import Blocked

APPS = {'com.tencent.mobileqq', 'com.tencent.mm'}
KINDS = {'auto_reply', 'proactive_dm', 'group_post'}
CHINA = timezone(timedelta(hours=8))
OPTOUT = re.compile(r'不要再联系|别再联系|不要回复|别再发|停止回复|退订|stop messaging|unsubscribe', re.I)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def incoming_reference(binding, message):
    stamp=message.get('time_label','')
    return digest(binding.account+'|'+binding.peer+'|'+(stamp+'|' if stamp else '')+message['text'])


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS social_bindings(
      id TEXT PRIMARY KEY,device TEXT NOT NULL,app TEXT NOT NULL,account TEXT NOT NULL,
      peer TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,updated REAL NOT NULL,
      UNIQUE(device,app,account,peer,kind));
    CREATE TABLE IF NOT EXISTS social_relations(
      binding_id TEXT PRIMARY KEY,opted_out INTEGER NOT NULL DEFAULT 0,
      summary TEXT NOT NULL DEFAULT '',updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS social_outbox(
      id TEXT PRIMARY KEY,device TEXT NOT NULL,session TEXT NOT NULL,binding_id TEXT NOT NULL,
      kind TEXT NOT NULL,trigger_id TEXT NOT NULL,intent_key TEXT NOT NULL,text TEXT NOT NULL,
      text_hash TEXT NOT NULL,motive TEXT NOT NULL,sources TEXT NOT NULL,status TEXT NOT NULL,
      created REAL NOT NULL,attempted REAL,observed REAL,evidence TEXT,
      UNIQUE(device,intent_key));
    CREATE INDEX IF NOT EXISTS social_quota ON social_outbox(device,created);
    ''')


@dataclass(frozen=True)
class Binding:
    app: str
    account: str
    peer: str
    kind: str
    existing_contact: bool
    observations: tuple
    verified_at: float
    source_type: str = 'v2_autonomous'
    route_hint: str = ''

    @property
    def identity(self):
        return (self.app, self.account, self.peer, self.kind)

    def validate(self, now=None):
        self.validate_common(now)
        if self.app not in APPS or self.kind not in {'dm','group'}:
            raise Blocked('SOCIAL_IDENTITY_UNVERIFIED_OR_EXPIRED')

    def validate_common(self,now=None):
        now = time.time() if now is None else now
        if (type(self.verified_at) not in (int,float) or not math.isfinite(self.verified_at)
                or not all(isinstance(s, str) and re.fullmatch(r'[A-Za-z0-9_@.:-]{3,160}', s) for s in (self.account, self.peer))
                or type(self.existing_contact) is not bool or not isinstance(self.route_hint,str) or len(self.route_hint)>100
                or not self.observations or any(not isinstance(s, str) or not s for s in self.observations)
                or not 0 <= now - self.verified_at <= 45):
            raise Blocked('SOCIAL_IDENTITY_UNVERIFIED_OR_EXPIRED')


class SocialPolicy:
    """Reload the operator's private configuration for every scope check."""
    def __init__(self, config):
        self.config = config
        self.path = config.data / 'social.private.json'
        self.group_test_window = None

    def read(self):
        if not self.path.exists():
            return {'enabled': False}
        if self.path.stat().st_size > 65536:
            raise Blocked('SOCIAL_POLICY_INVALID')
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise Blocked('SOCIAL_POLICY_INVALID') from None
        if not isinstance(value, dict) or value.get('serial') != self.config.serial or value.get('version') != 1:
            raise Blocked('SOCIAL_POLICY_DEVICE_MISMATCH')
        for key in ('accounts','contacts','groups','limits'):
            if key in value and not isinstance(value[key],dict):
                raise Blocked('SOCIAL_POLICY_INVALID')
        for container in (value.get('contacts',{}),value.get('groups',{})):
            if 'allowed' in container and (not isinstance(container['allowed'],dict)
                    or any(not isinstance(k,str) or not isinstance(v,list) or any(x not in KINDS for x in v) for k,v in container['allowed'].items())):
                raise Blocked('SOCIAL_POLICY_INVALID')
        return value

    def check(self, binding, kind, now=None):
        now = time.time() if now is None else now
        binding.validate(now)
        value = self.read()
        if value.get('enabled') is not True:
            raise Blocked('SOCIAL_DISABLED')
        if kind not in KINDS or (binding.kind == 'group') != (kind == 'group_post'):
            raise Blocked('SOCIAL_ACTION_KIND_MISMATCH')
        if value.get('accounts', {}).get(binding.app) != binding.account:
            raise Blocked('SOCIAL_ACCOUNT_CHANGED_OR_UNBOUND')
        contacts = value.get('contacts', {})
        allowed = False
        if binding.kind == 'dm' and binding.existing_contact:
            allowed = (contacts.get('mode') == 'all_existing_contacts' and contacts.get(kind) is True)
            allowed = allowed or kind in contacts.get('allowed', {}).get(binding.app + '/' + binding.peer, [])
        if binding.kind == 'group':
            groups=value.get('groups',{})
            allowed = (groups.get('mode')=='all_existing_groups' and groups.get('group_post') is True and binding.existing_contact)
            allowed = allowed or 'group_post' in groups.get('allowed', {}).get(binding.app + '/' + binding.peer, [])
        if not allowed:
            raise Blocked('SOCIAL_TARGET_OR_CAPABILITY_NOT_ALLOWED')
        if kind in {'proactive_dm', 'group_post'}:
            quiet = value.get('quiet_hours', ['23:00', '08:00'])
            if not isinstance(quiet, list) or len(quiet) != 2 or any(not isinstance(x, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', x) for x in quiet):
                raise Blocked('SOCIAL_QUIET_HOURS_INVALID')
            local = datetime.fromtimestamp(now, CHINA).strftime('%H:%M')
            start, end = quiet
            if start != end and ((start <= local < end) if start < end else (local >= start or local < end)):
                window=self.group_test_window
                if not (kind=='group_post' and window and window[0]==binding.identity
                        and 0<window[1]-now<=600):
                    raise Blocked('SOCIAL_QUIET_HOURS')
        limits = value.get('limits', {})
        hard = {'hour': 10, 'day': 50, 'conversation_day': 5, 'cooldown_seconds': 30,
                'proactive_cooldown_seconds': 172800}
        result = {}
        for key, default in hard.items():
            number = limits.get(key, default)
            if type(number) is not int or number < 1:
                raise Blocked('SOCIAL_LIMITS_INVALID')
            result[key] = min(number, default) if key in {'hour', 'day', 'conversation_day'} else max(number, default)
        return result


class SocialStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def bind(self, binding, now=None):
        binding.validate(now)
        bid = digest(self.store.serial + '\0' + '\0'.join(binding.identity))
        body = {'existing_contact': binding.existing_contact, 'observations': list(binding.observations),
                'verified_at': binding.verified_at, 'source_type': binding.source_type, 'route_hint':binding.route_hint}
        self.db.execute('INSERT INTO social_bindings VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated=excluded.updated',
            (bid, self.store.serial, binding.app, binding.account, binding.peer, binding.kind, json.dumps(body), time.time()))
        self.db.commit()
        return bid

    def targets(self, limit=8):
        rows = self.db.execute("SELECT b.id,b.app,b.kind,b.body,r.opted_out FROM social_bindings b LEFT JOIN social_relations r ON r.binding_id=b.id WHERE b.device=? AND b.kind IN ('dm','group') ORDER BY b.updated DESC LIMIT ?",
            (self.store.serial, limit)).fetchall()
        return [{'binding_id': r[0], 'app': r[1], 'kind': r[2], 'scope': 'bound_route_current_chat',
                 'source_type': json.loads(r[3])['source_type']} for r in rows if not r[4]]

    def public_experiences(self):
        """Only explicitly classified public content; never export global memories."""
        path=self.store.data/'social-public-sources.private.json'
        if not path.exists():return []
        if path.stat().st_size>16384:raise Blocked('SOCIAL_PUBLIC_SOURCE_MANIFEST_INVALID')
        try:
            manifest=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(manifest,dict) or manifest.get('version')!=1 or manifest.get('serial')!=self.store.serial or not isinstance(manifest.get('sources'),list) or len(manifest['sources'])>10:raise ValueError()
            result=[]
            for item in manifest['sources']:
                if not isinstance(item,dict):raise ValueError()
                if not re.fullmatch('[a-f0-9]{32}',item['segment_id']):raise ValueError()
                url=urlsplit(item['public_url'])
                if url.scheme!='https' or not url.hostname or url.username or url.password:raise ValueError()
                row=self.db.execute("SELECT c.source_type,c.body,c.summary,c.started FROM content_segments c JOIN sessions s ON s.id=c.session WHERE c.id=? AND s.serial=? AND c.status='summarized'",(item['segment_id'],self.store.serial)).fetchone()
                if not row or row[0] not in {'v2_autonomous','live_scripted'}:raise ValueError()
                if digest(row[2])!=item['summary_sha256']:raise ValueError()
                body=json.loads(row[1]);summary=json.loads(row[2])
                if not isinstance(body,dict) or not isinstance(summary,dict) or not isinstance(summary.get('summary'),str) or not isinstance(summary.get('uncertainty',''),str):raise ValueError()
                samples=body.get('samples',[])
                if not isinstance(samples,list) or not 1<=len(samples)<=10 or any(not isinstance(s,dict) or s.get('redacted') or s.get('incomplete') or not s.get('png_path') or hashlib.sha256(Path(s['png_path']).read_bytes()).hexdigest()!=s.get('png_sha256') for s in samples):raise ValueError()
                ids={s['observation_id'] for s in samples}
                if not summary.get('evidence_ids') or not set(summary['evidence_ids'])<=ids:raise ValueError()
                result.append({'source_id':'content:'+item['segment_id'],'public':True,'source_type':row[0],
                    'summary':summary['summary'][:700],'uncertainty':summary.get('uncertainty','')[:400],
                    'public_url':item['public_url'],'observed_at':row[3],'observation_ids':sorted(ids)})
            return result
        except (ValueError,TypeError,KeyError,OSError):
            raise Blocked('SOCIAL_PUBLIC_SOURCE_UNVERIFIED') from None

    def record_exchange(self,binding_id,kind,outbound_id):
        # A compact, target-local relationship fact, without copying other chats.
        summary=json.dumps({'last_outbound_id':outbound_id,'last_kind':kind,
            'state':'own_outgoing_UI_observed_not_read_receipt'},ensure_ascii=False)
        self.db.execute('INSERT INTO social_relations VALUES(?,0,?,?) ON CONFLICT(binding_id) DO UPDATE SET summary=excluded.summary,updated=excluded.updated',
            (binding_id,summary,time.time()))
        self.db.commit()

    def opt_out(self, binding_id, incoming_text):
        if not OPTOUT.search(incoming_text):
            return False
        self.db.execute("INSERT INTO social_relations VALUES(?,1,'Contact requested no further automatic messages',?) ON CONFLICT(binding_id) DO UPDATE SET opted_out=1,updated=excluded.updated", (binding_id, time.time()))
        self.db.commit()
        self.store.event('social_opted_out', {'binding_id': binding_id})
        return True

    def check_thread(self, binding_id, limits, now, exclude=None):
        relation = self.db.execute('SELECT opted_out FROM social_relations WHERE binding_id=?', (binding_id,)).fetchone()
        if relation and relation[0]:
            raise Blocked('SOCIAL_CONTACT_OPTED_OUT')
        if self.db.execute("SELECT 1 FROM social_outbox WHERE device=? AND binding_id=? AND status IN ('dispatch_attempted','delivery_unknown')", (self.store.serial, binding_id)).fetchone():
            raise Blocked('SOCIAL_THREAD_HAS_UNKNOWN_SEND')
        current=self.db.execute('SELECT app,account,kind FROM social_bindings WHERE id=?',(binding_id,)).fetchone()
        if current and current[0]=='com.tencent.mm' and current[2]=='group':
            # A group QR can rotate. A fresh fingerprint must not bypass an
            # unresolved send belonging to the same WeChat account's groups.
            if self.db.execute("SELECT 1 FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id WHERE o.device=? AND b.app=? AND b.account=? AND b.kind='group' AND o.status IN ('dispatch_attempted','delivery_unknown')",(self.store.serial,current[0],current[1])).fetchone():
                raise Blocked('SOCIAL_ACCOUNT_GROUP_HAS_UNKNOWN_SEND')
        clauses = "device=? AND status NOT IN ('cancelled_before_send') AND id!=?"
        params = (self.store.serial, exclude or '')
        for seconds, maximum in ((3600, limits['hour']), (86400, limits['day'])):
            if self.db.execute('SELECT count(*) FROM social_outbox WHERE ' + clauses + ' AND created>?', (*params, now-seconds)).fetchone()[0] >= maximum:
                raise Blocked('SOCIAL_RATE_LIMIT')
        if self.db.execute('SELECT count(*) FROM social_outbox WHERE ' + clauses + ' AND binding_id=? AND created>?',
            (*params, binding_id, now-86400)).fetchone()[0] >= limits['conversation_day']:
            raise Blocked('SOCIAL_CONVERSATION_RATE_LIMIT')
        last = self.db.execute('SELECT max(created) FROM social_outbox WHERE ' + clauses + ' AND binding_id=?', (*params, binding_id)).fetchone()[0]
        if limits['cooldown_seconds']>0 and last and now-last < limits['cooldown_seconds']:
            raise Blocked('SOCIAL_CONTACT_COOLDOWN')

    def prepare(self, policy, binding, kind, trigger_id, text, motive, sources, now=None):
        now = time.time() if now is None else now
        limits = policy.check(binding, kind, now)
        if (not isinstance(text, str) or not 1 <= len(text) <= 300
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in text)
                or re.search(r'sk-[A-Za-z0-9_-]{12,}|(?:验证码|password|api[_ -]?key)\s*[:=：]?\s*[A-Za-z0-9_-]{4,}', text, re.I)
                or not isinstance(motive, str) or not 1 <= len(motive) <= 200
                or not isinstance(trigger_id, str) or not trigger_id or len(trigger_id) > 160
                or not isinstance(sources, list) or any(not isinstance(x, str) for x in sources)
                or kind == 'proactive_dm' and not sources):
            raise Blocked('SOCIAL_DRAFT_OR_MOTIVE_INVALID')
        bid = self.bind(binding, now)
        key = digest(bid + '\0' + kind + '\0' + trigger_id)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM social_outbox WHERE device=? AND intent_key=?', (self.store.serial, key)).fetchone():
                raise Blocked('SOCIAL_INTENT_ALREADY_RECORDED')
            if self.db.execute('SELECT 1 FROM social_outbox WHERE device=? AND binding_id=? AND text_hash=? AND created>?',
                (self.store.serial, bid, digest(text), now-86400)).fetchone():
                raise Blocked('SOCIAL_DUPLICATE_CONTENT')
            self.check_thread(bid, limits, now)
            if kind == 'proactive_dm':
                last = self.db.execute("SELECT max(created) FROM social_outbox WHERE device=? AND binding_id=? AND kind='proactive_dm'", (self.store.serial, bid)).fetchone()[0]
                if last and now-last < limits['proactive_cooldown_seconds']:
                    raise Blocked('SOCIAL_PROACTIVE_COOLDOWN')
            oid = uuid.uuid4().hex
            self.db.execute("INSERT INTO social_outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,'prepared',?,NULL,NULL,NULL)",
                (oid, self.store.serial, self.store.session, bid, kind, trigger_id, key, text, digest(text), motive, json.dumps(sources), now))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self.store.event('social_prepared', {'outbound_id': oid, 'binding_id': bid, 'action_kind': kind, 'text_sha256': digest(text), 'intent_key': key})
        return oid

    def transition(self, outbound_id, expected, status, evidence=None):
        allowed = {('prepared','typed'),('prepared','cancelled_before_send'),('typed','cancelled_before_send'),
                   ('prepared','voice_ready'),('voice_ready','cancelled_before_send'),('voice_ready','dispatch_attempted'),
                   ('prepared','card_ready'),('card_ready','cancelled_before_send'),('card_ready','dispatch_attempted'),
                   ('typed','dispatch_attempted'),('dispatch_attempted','sent_observed'),
                   ('dispatch_attempted','delivery_unknown')}
        if (expected, status) not in allowed:
            raise Blocked('SOCIAL_STATE_TRANSITION_INVALID')
        if 'voice_ready' in (expected,status):
            payload=self.db.execute('SELECT sources FROM social_outbox WHERE id=? AND device=?',(outbound_id,self.store.serial)).fetchone()
            if not payload or 'native:qq-record' not in json.loads(payload[0]):raise Blocked('SOCIAL_VOICE_PAYLOAD_REQUIRED')
        if 'card_ready' in (expected,status):
            payload=self.db.execute('SELECT kind,sources FROM social_outbox WHERE id=? AND device=?',(outbound_id,self.store.serial)).fetchone()
            if not payload or payload[0]!='group_video_share' or 'native:official-share-card' not in json.loads(payload[1]):raise Blocked('SOCIAL_NATIVE_CARD_PAYLOAD_REQUIRED')
        cursor = self.db.execute('UPDATE social_outbox SET status=?,evidence=?,attempted=CASE WHEN ?=\'dispatch_attempted\' THEN ? ELSE attempted END,observed=CASE WHEN ?=\'sent_observed\' THEN ? ELSE observed END WHERE id=? AND device=? AND status=?',
            (status, json.dumps(evidence or {}), status, time.time(), status, time.time(), outbound_id, self.store.serial, expected))
        self.db.commit()
        if cursor.rowcount != 1:
            raise Blocked('SOCIAL_SEND_ALREADY_ATTEMPTED_OR_STATE_CHANGED')
        claim=('own_QQ_client_record_observed_not_read_receipt' if (evidence or {}).get('transport')=='napcat_record'
            else 'own_outgoing_UI_observed_not_read_receipt') if status=='sent_observed' else None
        self.store.event('social_' + status, {'outbound_id': outbound_id, 'evidence': evidence or {}, 'delivery_claim': claim})

    def before_send(self, policy, binding, outbound_id, now=None):
        now = time.time() if now is None else now
        row = self.db.execute('SELECT binding_id,kind,status,created FROM social_outbox WHERE id=? AND device=?', (outbound_id, self.store.serial)).fetchone()
        if not row or row[2] not in {'typed','card_ready','voice_ready'} or not 0 <= now-row[3] <= 300:
            raise Blocked('SOCIAL_SEND_NOT_PREPARED_OR_EXPIRED')
        bid = digest(self.store.serial + '\0' + '\0'.join(binding.identity))
        if bid != row[0]:
            raise Blocked('SOCIAL_CONVERSATION_CHANGED')
        limits = policy.check(binding, row[1], now)
        self.check_thread(bid, limits, now, outbound_id)
        return row[1]

    def resume_pre_send(self,policy,binding,outbound_id,observation_id):
        """Operator recovery only: no send was ever attempted for this draft."""
        now=time.time()
        row=self.db.execute('SELECT binding_id,kind,status,attempted,created FROM social_outbox WHERE id=? AND device=?',(outbound_id,self.store.serial)).fetchone()
        bid=digest(self.store.serial+'\0'+'\0'.join(binding.identity))
        if not row or row[0]!=bid or row[2]!='cancelled_before_send' or row[3] is not None:
            raise Blocked('SOCIAL_DRAFT_NOT_KNOWN_UNSENT')
        limits=policy.check(binding,row[1],now)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.check_thread(bid,limits,now,outbound_id)
            if row[1]=='proactive_dm':
                last=self.db.execute("SELECT max(created) FROM social_outbox WHERE device=? AND binding_id=? AND kind='proactive_dm' AND id!=? AND status!='cancelled_before_send'",(self.store.serial,bid,outbound_id)).fetchone()[0]
                if last and now-last<limits['proactive_cooldown_seconds']:raise Blocked('SOCIAL_PROACTIVE_COOLDOWN')
            cursor=self.db.execute("UPDATE social_outbox SET status='typed',created=?,evidence=? WHERE id=? AND status='cancelled_before_send' AND attempted IS NULL",
                (now,json.dumps({'resumed_from_known_unsent':True,'original_created':row[4],'observation_id':observation_id}),outbound_id))
            if cursor.rowcount!=1:raise Blocked('SOCIAL_DRAFT_STATE_CHANGED')
            self.db.commit()
        except BaseException:
            self.db.rollback();raise
        self.store.event('social_known_unsent_resumed',{'outbound_id':outbound_id,'original_created':row[4],'observation_id':observation_id})
